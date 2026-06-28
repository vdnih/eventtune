"""
DataIntegrationAgent — Layer 1: データ統合パイプライン（OSI 星座型）

入力ファイルを読み、含まれるエンティティ（5マスタ＋ファクト）へ分解して Firestore に保存する。
docs/INGESTION_MAPPING.md に従い、ファイルは「レコードの容れ物」であり、イベントは経路キーでなく
リンク（FK）として扱う。リンクは「列 → ファイル既定（ヒント）→ 名寄せ（安定ID）」で解決する。

  パス A (表形式 CSV/Excel): SchemaMapper でカラム/リンクを判定 → OntologyMapper で行分解
  パス B (非構造化 TXT):      DocumentExtractor でエンティティ抽出 → OntologyMapper で変換

決定論的な分解は OntologyMapper（AI 不使用）。各マスタの appeal_summary / appeal_vector は
本モジュールが semantic_search（AI）で後付けする。リンク先マスタは identity スタブを
merge 書き込みして存在を保証する（後から詳細ファイルが merge される）。
"""

import asyncio
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from google import genai
from google.genai import types
from pydantic import BaseModel

import semantic_search
from agents.ontology_mapper import OntologyMapper
from metering import record_llm_response
from space import SpaceContext
from ontology import (
    Account,
    ColumnMappingResult,
    Content,
    CostItem,
    DocumentExtractionResult,
    Event,
    EventAttendance,
    IntegrationJob,
    Person,
    Product,
    ProductInterest,
    TransformationSummary,
)

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite"
_mapper = OntologyMapper()
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _hint_block(hint: str | None) -> str:
    hint = (hint or "").strip()
    return f"\n\n【ユーザーのヒント（リンク解決・種別判定の補正に使う）】\n{hint}\n" if hint else ""


# ── パスA: SchemaMapper ──────────────────────────────────────────────────────
#
# column_map はキーが CSV カラム名で可変のため response_schema は使わず JSON モードのみ。

_SCHEMA_MAPPER_PROMPT = """\
あなたはデータ統合の専門家です。CSV のヘッダーとサンプル行（とユーザーのヒント）を読み、
このテーブルがどのエンティティ種別かを判定し、各カラムをオントロジーフィールドへマッピングしてください。

【エンティティ種別とフィールド】
persons（人物リスト。氏名・会社・役職などを含む）:
  name / name_last / name_first / company_name / department / job_title / email / extracted_challenge
  __engagement_signal（判定ランク A/B/C）/ __temperature_signal（温度感）/ __product_signal（関心製品名）
  __memo / __needs / __caution（notes へ集約される所感・要望・注意）
accounts（企業マスタ）: account_name（または company_name）/ industry_type / company_size
events（イベントマスタ）: name / event_type（展示会/セミナー/プライベートイベント）/ status / venue /
  event_date（YYYY-MM-DD）/ event_date_end / total_budget / target_contact_count / description
products（製品マスタ）: product_name / product_category
contents（素材マスタ）: name / content_type / url / description
cost_items（費用）: category / description / amount_jpy / vendor_name / invoice_date

【リンク列 link_columns】
行ごとに異なるリンク先マスタを識別する列があれば {種別: カラム名} で設定する:
  "event": イベント名・展示会名の列 / "account": 会社名の列 / "product": 製品名の列

【ファイル既定リンク default_links】
行に該当列が無いが、ファイル全体が特定マスタに属するとヒント等から分かる場合 {種別: 名称} を設定する。
  例: ヒント「2025秋展示会の参加者リスト」→ {"event": "2025秋展示会"}

【ルール】
- entity_type は persons / accounts / events / products / contents / cost_items のいずれか
- column_map にすべてのカラムを含める（マッピングできないものは unmapped_columns へ）
- __ プレフィックスは OntologyMapper でさらに変換される特殊フィールド
- 推測でリンクを作らない。根拠がある場合のみ link_columns / default_links を設定する

【出力形式】次の形の JSON オブジェクトのみを返す（説明やコードフェンスは不要）:
{
  "entity_type": "...",
  "column_map": { "CSVカラム名": "オントロジーフィールド名", ... },
  "unmapped_columns": [ ... ],
  "link_columns": { "event": "カラム名", ... },
  "default_links": { "event": "名称", ... }
}
"""


def _parse_json_response(text: str) -> dict:
    """JSON モードのレスポンス文字列を dict にパースする（フェンス・ノイズに防御的）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        result = json.loads(text[start : end + 1])
    return result if isinstance(result, dict) else {}


def _str_map(raw: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in raw.items() if v} if isinstance(raw, dict) else {}


async def run_schema_mapper(
    headers: list[str], sample_rows: list[dict],
    space: Optional[SpaceContext] = None,
    hint: str | None = None,
) -> ColumnMappingResult:
    sample_text = json.dumps(sample_rows[:5], ensure_ascii=False, indent=2)
    prompt = (
        f"{_SCHEMA_MAPPER_PROMPT}{_hint_block(hint)}\n\n"
        f"【カラムヘッダー】\n{headers}\n\n"
        f"【サンプルデータ（先頭5行）】\n{sample_text}"
    )
    response = await _get_client().aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    if space is not None:
        record_llm_response(space, _MODEL, response)
    parsed = _parse_json_response(response.text)
    unmapped = parsed.get("unmapped_columns") or []
    return ColumnMappingResult(
        entity_type=str(parsed.get("entity_type", "")),
        column_map=_str_map(parsed.get("column_map")),
        unmapped_columns=[str(c) for c in unmapped],
        link_columns=_str_map(parsed.get("link_columns")),
        default_links=_str_map(parsed.get("default_links")),
    )


# ── パスB: DocumentExtractor ─────────────────────────────────────────────────

class _EngagementCountsExtraction(BaseModel):
    appointment_booked: Optional[int] = None
    high_intent: Optional[int] = None
    nurturing: Optional[int] = None


class _EventExtraction(BaseModel):
    name: Optional[str] = None
    event_type: Optional[str] = None
    status: Optional[str] = None
    venue: Optional[str] = None
    event_date: Optional[str] = None
    event_date_end: Optional[str] = None
    booth_number: Optional[str] = None
    total_budget: Optional[float] = None
    target_contact_count: Optional[int] = None
    description: Optional[str] = None


class _EventKPIExtraction(BaseModel):
    total_visitors_to_booth: Optional[int] = None
    total_contacts_collected: Optional[int] = None
    appointments_booked: Optional[int] = None
    demo_sessions_held: Optional[int] = None
    follow_email_open_rate: Optional[float] = None
    follow_email_reply_rate: Optional[float] = None
    pipeline_value_jpy: Optional[float] = None
    closed_deals_3m: Optional[int] = None
    closed_revenue_3m_jpy: Optional[float] = None
    contacts_by_engagement: Optional[_EngagementCountsExtraction] = None


class _CostItemExtraction(BaseModel):
    category: Optional[str] = None
    description: Optional[str] = None
    amount_jpy: Optional[float] = None
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None


class _SatisfactionScoreExtraction(BaseModel):
    category: Optional[str] = None
    avg_score: Optional[float] = None
    response_count: Optional[int] = None


class _SurveyResponseExtraction(BaseModel):
    total_responses: Optional[int] = None
    nps_score: Optional[float] = None
    nps_promoters: Optional[int] = None
    nps_passives: Optional[int] = None
    nps_detractors: Optional[int] = None
    satisfaction_scores: list[_SatisfactionScoreExtraction] = []
    verbatim_positives: list[str] = []
    verbatim_negatives: list[str] = []
    verbatim_suggestions: list[str] = []


class _ContentAssetExtraction(BaseModel):
    asset_id: Optional[str] = None
    content_type: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    linked_event_id: Optional[str] = None


class _DocumentExtractionResponse(BaseModel):
    detected_entity_types: list[str] = []
    events: list[_EventExtraction] = []
    event_kpi: Optional[_EventKPIExtraction] = None
    cost_items: Optional[list[_CostItemExtraction]] = None
    survey_response: Optional[_SurveyResponseExtraction] = None
    content_assets: Optional[list[_ContentAssetExtraction]] = None


_DOCUMENT_EXTRACTOR_PROMPT = """\
あなたはイベントマーケティングデータの統合専門家です。
以下のドキュメントを読み、含まれる情報をオントロジーのエンティティに変換してください。
1つのドキュメントから複数のエンティティが抽出されることがあります。
イベントは複数記載されている場合もあります（例: 年間イベント計画書）。その場合はすべて抽出してください。

【オントロジーのエンティティとフィールド】

Event（複数可）:
  name, event_type（展示会/セミナー/プライベートイベント）, status（計画中/開催中/終了）,
  venue, event_date（YYYY-MM-DD）, event_date_end（YYYY-MM-DD）, booth_number,
  total_budget（数値のみ）, target_contact_count（数値）, description（所感・目的・担当者メモ等）

EventKPI:
  total_visitors_to_booth, total_contacts_collected, appointments_booked, demo_sessions_held,
  follow_email_open_rate（0.0〜1.0）, follow_email_reply_rate（0.0〜1.0）,
  pipeline_value_jpy, closed_deals_3m, closed_revenue_3m_jpy,
  contacts_by_engagement: { appointment_booked, high_intent, nurturing }

CostItem（複数可）:
  category（ブース出展料/ブース装飾・設営/機材・備品/人件費・派遣/交通・宿泊/印刷・販促物/飲食・接待/その他）,
  description, amount_jpy（数値のみ）, vendor_name, invoice_date（YYYY-MM-DD）

SurveyResponse:
  total_responses, nps_score（-100〜100）, nps_promoters, nps_passives, nps_detractors,
  satisfaction_scores: [{ category, avg_score, response_count }],
  verbatim_positives/negatives/suggestions: [コメント文字列]

ContentAsset（複数可）:
  asset_id, content_type（未来のセミナー（募集中）/未来のイベント（募集中）/資料・ホワイトペーパー/導入事例）,
  name, description, url, linked_event_id

【ルール】
- ドキュメントに含まれるエンティティのみ detected_entity_types に列挙する
- 数値のカンマ・通貨記号・単位を除去し、パーセント(61%)は小数(0.61)に変換する
- 構造化できない文脈・所感・メモは Event.description に集約する
- 含まれない情報は null にする（推測で埋めない）
"""


async def run_document_extractor(
    text: str,
    space: Optional[SpaceContext] = None,
    hint: str | None = None,
) -> DocumentExtractionResult:
    prompt = f"{_DOCUMENT_EXTRACTOR_PROMPT}{_hint_block(hint)}\n\n【ドキュメント】\n{text}"
    response = await _get_client().aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_DocumentExtractionResponse,
        ),
    )
    if space is not None:
        record_llm_response(space, _MODEL, response)
    parsed = _DocumentExtractionResponse.model_validate_json(response.text)
    return DocumentExtractionResult(
        detected_entity_types=parsed.detected_entity_types,
        events=[e.model_dump() for e in parsed.events] if parsed.events else [],
        event_kpi=parsed.event_kpi.model_dump() if parsed.event_kpi else None,
        cost_items=[c.model_dump() for c in parsed.cost_items] if parsed.cost_items else None,
        survey_response=parsed.survey_response.model_dump() if parsed.survey_response else None,
        content_assets=(
            [a.model_dump() for a in parsed.content_assets] if parsed.content_assets else None
        ),
    )


# ── ファイル種別判定・読み込み ────────────────────────────────────────────────

def _is_tabular(filename: str) -> bool:
    return filename.lower().endswith((".csv", ".xlsx", ".xls"))


def _read_tabular(filename: str, content: bytes) -> tuple[list[str], list[dict]]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str)
    else:
        df = pd.read_excel(io.BytesIO(content), dtype=str)
    df = df.fillna("")
    return list(df.columns), df.to_dict(orient="records")


def _read_text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


# ── Firestore パス解決 ─────────────────────────────────────────────────────────

_KPI_SURVEY_FIELDS = frozenset({
    "total_visitors_to_booth", "total_contacts_collected", "appointments_booked",
    "demo_sessions_held", "follow_email_open_rate", "follow_email_reply_rate",
    "pipeline_value_jpy", "closed_deals_3m", "closed_revenue_3m_jpy",
    "nps_score", "total_survey_responses", "updated_at",
})


def _entity_to_firestore_path(entity: Any) -> tuple[str | None, str | None]:
    if isinstance(entity, Person):
        return "persons", entity.person_id
    if isinstance(entity, Account):
        return "accounts", entity.account_id
    if isinstance(entity, EventAttendance):
        return "event_attendances", entity.attendance_id
    if isinstance(entity, ProductInterest):
        return "product_interests", entity.interest_id
    if isinstance(entity, Product):
        return "products", entity.product_id
    if isinstance(entity, Event):
        return "events", entity.event_id
    if isinstance(entity, CostItem):
        return f"events/{entity.event_id}/costs", entity.cost_id
    if isinstance(entity, Content):
        return "contents", entity.content_id
    return None, None


# ── appeal 生成（AI, 非ブロッキング）─────────────────────────────────────────

def _appeal_spec(entity: Any) -> tuple[str, dict] | None:
    """appeal を生成すべきエンティティなら (kind, payload) を返す。

    KPI/Survey パッチ用 Event（name 空）や identity スタブはここに来ない（payload なし）。
    """
    if isinstance(entity, Person):
        return "person", {
            "name": entity.name, "department": entity.department,
            "job_title": entity.job_title, "extracted_challenge": entity.extracted_challenge,
            "notes": entity.notes,
        }
    if isinstance(entity, Event) and entity.name:
        return "event", {
            "name": entity.name, "event_type": entity.event_type.value,
            "venue": entity.venue, "description": entity.description,
        }
    if isinstance(entity, Product) and entity.product_name:
        return "product", {
            "product_name": entity.product_name, "product_category": entity.product_category,
        }
    if isinstance(entity, Content) and entity.content_name:
        return "content", {
            "content_name": entity.content_name, "content_type": entity.content_type.value,
            "description": entity.description,
        }
    return None


async def _apply_appeal(entities: list[Any], space: Optional[SpaceContext]) -> None:
    """対象マスタ/Person に appeal_summary / appeal_vector を並列生成して付与する。"""
    targets: list[tuple[Any, str, dict]] = []
    for e in entities:
        spec = _appeal_spec(e)
        if spec is not None:
            targets.append((e, spec[0], spec[1]))
    if not targets:
        return
    results = await asyncio.gather(
        *[semantic_search.build_appeal(kind, payload, space=space) for _, kind, payload in targets]
    )
    for (entity, _kind, _payload), (summary, vector) in zip(targets, results):
        entity.appeal_summary = summary
        entity.appeal_vector = vector


# ── メイン処理 ────────────────────────────────────────────────────────────────

def _dedup_masters(refs: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for kind, mid, name in refs:
        key = (kind, mid)
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, mid, name))
    return out


def _write_link_stubs(db: Any, refs: list[tuple[str, str, str]], space_id: str) -> None:
    """リンク先マスタの identity スタブを merge 書き込みし、存在を保証する。

    identity フィールドのみ書くため、別ファイル由来の詳細（appeal/種別等）を上書きしない。
    """
    paths = {
        "event": ("events", "event_id", "name"),
        "product": ("products", "product_id", "product_name"),
        "account": ("accounts", "account_id", "account_name"),
    }
    for kind, mid, name in _dedup_masters(refs):
        spec = paths.get(kind)
        if not spec:
            continue
        collection, id_field, name_field = spec
        db.document(f"{collection}/{mid}").set(
            {id_field: mid, "space_id": space_id, name_field: name}, merge=True
        )


async def process_file(
    filename: str,
    content: bytes,
    hint: str | None,
    batch_id: str,
    db: Any,  # space.ScopedClient（スペース前置済み）
    space: Optional[SpaceContext] = None,
) -> tuple[list[Any], IntegrationJob]:
    """1ファイルを処理してオントロジーエンティティを生成し、Firestore に保存する。"""
    job_id = _new_id("job_")
    space_id = space.space_id if space is not None else ""
    logger.info("process_file: filename=%s job_id=%s hint=%r", filename, job_id, hint)

    column_mapping = None
    raw_extraction_dict = None

    if _is_tabular(filename):
        headers, rows = _read_tabular(filename, content)
        column_mapping = await run_schema_mapper(headers, rows, space=space, hint=hint)
        logger.info("schema_mapper: entity_type=%s cols=%d link=%s default=%s",
                    column_mapping.entity_type, len(column_mapping.column_map),
                    column_mapping.link_columns, column_mapping.default_links)
        result = _mapper.map_rows(column_mapping, rows, space_id=space_id, job_id=job_id)
    else:
        text = _read_text(content)
        extraction = await run_document_extractor(text, space=space, hint=hint)
        raw_extraction_dict = extraction.model_dump()
        logger.info("document_extractor: detected=%s", extraction.detected_entity_types)
        result = _mapper.map_extraction(extraction, space_id=space_id, job_id=job_id)

    # appeal_summary / appeal_vector を付与（非ブロッキング）
    await _apply_appeal(result.entities, space)

    # リンク先マスタの identity スタブを保証
    _write_link_stubs(db, result.referenced_masters, space_id)

    # エンティティを書き込み
    created_ids: dict[str, list[str]] = {}
    for entity in result.entities:
        if isinstance(entity, Event) and not entity.name and not entity.created_at:
            # KPI/Survey パッチ — 既存 Event へ該当フィールドだけ merge
            patch = {k: v for k, v in entity.model_dump().items()
                     if k in _KPI_SURVEY_FIELDS and v is not None}
            if patch:
                db.document(f"events/{entity.event_id}").set(patch, merge=True)
        else:
            collection, doc_id = _entity_to_firestore_path(entity)
            if collection and doc_id:
                db.document(collection + "/" + doc_id).set(entity.model_dump(), merge=True)
                created_ids.setdefault(type(entity).__name__, []).append(doc_id)

    logger.info("process_file done: created=%s", {k: len(v) for k, v in created_ids.items()})

    summary = _summarize_transformations(result.entities, result.skipped)
    job = IntegrationJob(
        job_id=job_id,
        space_id=space_id,
        filenames=[filename],
        hint=hint or "",
        status="done",
        created_entities={k: len(v) for k, v in created_ids.items()},
        resolved_links=[{"kind": k, "id": i, "name": n}
                        for k, i, n in _dedup_masters(result.referenced_masters)],
        column_mapping=column_mapping,
        raw_extraction=raw_extraction_dict,
        transformations=result.transformations,
        skipped_records=result.skipped,
        transformation_summary=summary,
        created_at=_now_iso(),
    )
    db.collection("integration_jobs").document(job.job_id).set(job.model_dump())

    return result.entities, job


def _summarize_transformations(
    entities: list[Any], skipped: list[Any]
) -> TransformationSummary:
    entity_counts: dict[str, int] = {}
    engagement_breakdown: dict[str, int] = {}
    product_breakdown: dict[str, int] = {}
    for entity in entities:
        entity_counts[type(entity).__name__] = entity_counts.get(type(entity).__name__, 0) + 1
        if isinstance(entity, Person) and entity.engagement_level:
            lvl = entity.engagement_level.value
            engagement_breakdown[lvl] = engagement_breakdown.get(lvl, 0) + 1
        if isinstance(entity, ProductInterest):
            product_breakdown[entity.product_id] = product_breakdown.get(entity.product_id, 0) + 1
    return TransformationSummary(
        entity_counts=entity_counts,
        engagement_breakdown=engagement_breakdown,
        product_breakdown=product_breakdown,
        skipped_count=len(skipped),
    )


# ── バッチ処理（複数ファイルの横断統合）─────────────────────────────────────────

@dataclass
class BatchFileResult:
    """バッチ内の1ファイルの処理結果。"""

    filename: str
    status: str  # "done" | "error"
    job_id: str | None = None
    created_entities: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "status": self.status,
            "job_id": self.job_id,
            "created_entities": self.created_entities,
            "error": self.error,
        }


async def process_batch(
    files: list[tuple[str, bytes]],
    batch_id: str,
    db: Any,  # space.ScopedClient（スペース前置済み）
    hint: str | None = None,
    space: Optional[SpaceContext] = None,
) -> list[BatchFileResult]:
    """複数ファイルを並列に処理する。

    hint はユーザーの自然言語ヒント（曖昧なリンク解決・スコープ指定の補正）。全ファイル共通。
    space は LLM トークン計測に使う（None なら計測しない）。
    """
    results = await asyncio.gather(
        *[_process_one(fn, content, hint, batch_id, db, space=space) for fn, content in files]
    )
    return list(results)


async def _process_one(
    filename: str,
    content: bytes,
    hint: str | None,
    batch_id: str,
    db: Any,
    space: Optional[SpaceContext] = None,
) -> BatchFileResult:
    """1ファイルを process_file で処理し、部分失敗を吸収して結果を返す。"""
    try:
        entities, job = await process_file(filename, content, hint, batch_id, db, space=space)
        counts: dict[str, int] = {}
        for entity in entities:
            counts[type(entity).__name__] = counts.get(type(entity).__name__, 0) + 1
        return BatchFileResult(
            filename=filename, status="done", job_id=job.job_id, created_entities=counts,
        )
    except Exception as e:
        logger.exception("process_one failed: filename=%s error=%s", filename, e)
        return BatchFileResult(filename=filename, status="error", error=str(e)[:500])
