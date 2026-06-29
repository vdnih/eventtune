"""
DataIntegrationAgent — Layer 1: データ統合パイプライン（OSI 星座型・依存順の多段）

入力ファイルを読み、含まれるエンティティ（5マスタ＋ファクト）へ分解して Firestore に保存する。
ADR-011 / docs/INGESTION_MAPPING.md に従い、取り込みを依存順の多段で行う:

  観測着地+解釈（並列） → 確定(conform: マスタ) → 結合(bind: person＋ファクト) → 導出(derive)

  パス A (表形式 CSV/Excel): SchemaMapper でカラム/リンクを判定 → OntologyMapper で観測へ解釈
  パス B (非構造化 TXT):      DocumentExtractor でエンティティ抽出 → OntologyMapper で解釈

AI=解釈（種別/列写像/抽出）、決定論 Python=正規化照合・find-or-create・永続。マスタ・リンクの
同一性は安定ID（名前ハッシュ）ではなく「スペース内の実在エンティティを自然キーで検索する
find-or-create」（EntityResolver）に一本化した。Person.appeal は全 attendance を集約して導出する。
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
from agents.ontology_mapper import MapResult, OntologyMapper, _normalize_name
from metering import record_llm_response
from space import SpaceContext
from ontology import (
    Account,
    ColumnMappingResult,
    ContactStage,
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
persons（人物リスト。氏名・会社・役職などを含む。1行＝1接客の観測）:
  name / name_last / name_first / company_name / department / job_title / email
  __engagement_signal（判定ランク A/B/C）/ __temperature_signal（温度感）/ __product_signal（関心製品名）
  __event_owner（接客担当者）/ __challenge（その接客で把握した課題感）
  __memo / __needs / __caution（接客メモへ集約される所感・要望・注意）
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


# ── 同一性解決（EntityResolver）─────────────────────────────────────────────────
#
# 安定ID（名前ハッシュ）を廃し、スペース内の実在エンティティを自然キーで検索する
# find-or-create に一本化（ADR-011）。表記揺れは _normalize_name で吸収し、完全一致 →
# 一意な包含一致（fuzzy）→ 新規 UUID 採番、の順で解決する。小規模 O(N) で十分。

class EntityResolver:
    """種別ごとに {正規化キー: uuid} を保持し、名前から既存/新規の UUID を解決する。

    seed: 既存マスタの [(自然キー文字列, uuid)]。コンストラクタでスペースから読み込む。
    fuzzy=True のときのみ包含一致のフォールバックを行う（マスタ向け。person/fact は False）。
    """

    def __init__(
        self, kind: str, seed: list[tuple[str, str]], id_prefix: str, fuzzy: bool = True
    ):
        self.kind = kind
        self._id_prefix = id_prefix
        self._fuzzy = fuzzy
        self._by_key: dict[str, str] = {}
        self._entries: list[tuple[str, str]] = []  # (norm_key, uuid)
        for name, _id in seed:
            self._index(name, _id)
        self.created: dict[str, str] = {}  # uuid -> display name（新規採番分）
        self.log: list[dict] = []           # resolved_links 監査用

    def _index(self, name: str, _id: str) -> None:
        key = _normalize_name(name)
        if key and key not in self._by_key:
            self._by_key[key] = _id
            self._entries.append((key, _id))

    def resolve(self, name: str, display: str = "") -> tuple[str | None, bool]:
        """名前 → (uuid, created)。名前が空なら (None, False)。

        完全一致（正規化）→ 一意な包含一致（fuzzy 時）→ 新規 UUID 採番。
        """
        key = _normalize_name(name)
        if not key:
            return None, False
        if key in self._by_key:
            return self._by_key[key], False
        if self._fuzzy:
            cands = {_id for k, _id in self._entries if k and (k in key or key in k)}
            if len(cands) == 1:
                _id = next(iter(cands))
                self._by_key[key] = _id  # 以降の照合を高速化
                return _id, False
        new_id = _new_id(self._id_prefix)
        self._by_key[key] = new_id
        self._entries.append((key, new_id))
        self.created[new_id] = display or name
        self.log.append({"kind": self.kind, "id": new_id, "name": display or name})
        return new_id, True


def _load_existing(
    db: Any, collection: str, name_field: str, id_field: str
) -> list[tuple[str, str]]:
    """既存マスタを [(name, uuid)] で読み込み、resolver のシードにする。"""
    out: list[tuple[str, str]] = []
    try:
        for doc in db.collection(collection).get():
            d = doc.to_dict() or {}
            name = d.get(name_field, "")
            _id = d.get(id_field) or doc.id
            if name and _id:
                out.append((name, _id))
    except Exception:
        logger.exception("_load_existing failed: collection=%s", collection)
    return out


def _person_key(name: str, email: str, company: str) -> str:
    """person の自然キー: 正規化 email、無ければ 正規化(name)|正規化(company)。"""
    em = _normalize_name(email)
    if em:
        return em
    return f"{_normalize_name(name)}|{_normalize_name(company)}"


def _load_existing_persons(db: Any) -> list[tuple[str, str]]:
    """既存 person を [(自然キー, uuid)] で読み込む（email 優先、無ければ name）。"""
    out: list[tuple[str, str]] = []
    try:
        for doc in db.collection("persons").get():
            d = doc.to_dict() or {}
            pid = d.get("person_id") or doc.id
            if not pid:
                continue
            key = _person_key(d.get("name", ""), d.get("email") or "", "")
            if key.strip("|"):
                out.append((key, pid))
    except Exception:
        logger.exception("_load_existing_persons failed")
    return out


# ── 解釈（観測着地）─────────────────────────────────────────────────────────────

@dataclass
class _FileInterpretation:
    """1ファイルの解釈結果（中間レコード）。永続はバッチ横断の conform/bind が行う。"""
    filename: str
    job_id: str | None = None
    map_result: MapResult | None = None
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


_KIND_LABEL = {
    "events": "Event", "accounts": "Account", "products": "Product",
    "contents": "Content", "cost_items": "CostItem", "event_patch": "EventPatch",
}


def _summarize(result: MapResult) -> TransformationSummary:
    entity_counts: dict[str, int] = {}
    engagement_breakdown: dict[str, int] = {}
    if result.person_observations:
        entity_counts["Person"] = len(result.person_observations)
    for obs in result.person_observations:
        if obs.engagement_level:
            lvl = obs.engagement_level.value
            engagement_breakdown[lvl] = engagement_breakdown.get(lvl, 0) + 1
    for rec in result.records:
        label = _KIND_LABEL.get(rec.kind, rec.kind)
        entity_counts[label] = entity_counts.get(label, 0) + 1
    return TransformationSummary(
        entity_counts=entity_counts,
        engagement_breakdown=engagement_breakdown,
        product_breakdown={},
        skipped_count=len(result.skipped),
    )


async def _interpret_file(
    filename: str,
    content: bytes,
    hint: str | None,
    db: Any,
    space: Optional[SpaceContext] = None,
) -> _FileInterpretation:
    """1ファイルを読み・AI 解釈し、中間レコード（MapResult）を返す。永続しない。

    解釈の監査（column_mapping / raw_extraction / transformations / skipped）は
    integration_jobs に子ジョブとして残す。
    """
    job_id = _new_id("job_")
    space_id = space.space_id if space is not None else ""
    try:
        column_mapping = None
        raw_extraction_dict = None
        if _is_tabular(filename):
            headers, rows = _read_tabular(filename, content)
            column_mapping = await run_schema_mapper(headers, rows, space=space, hint=hint)
            logger.info("schema_mapper: file=%s entity_type=%s cols=%d link=%s default=%s",
                        filename, column_mapping.entity_type, len(column_mapping.column_map),
                        column_mapping.link_columns, column_mapping.default_links)
            result = _mapper.map_rows(column_mapping, rows, space_id=space_id, job_id=job_id)
        else:
            text = _read_text(content)
            extraction = await run_document_extractor(text, space=space, hint=hint)
            raw_extraction_dict = extraction.model_dump()
            logger.info("document_extractor: file=%s detected=%s",
                        filename, extraction.detected_entity_types)
            result = _mapper.map_extraction(extraction, space_id=space_id, job_id=job_id)

        summary = _summarize(result)
        job = IntegrationJob(
            job_id=job_id, space_id=space_id, filenames=[filename], hint=hint or "",
            status="done", created_entities=summary.entity_counts,
            column_mapping=column_mapping, raw_extraction=raw_extraction_dict,
            transformations=result.transformations, skipped_records=result.skipped,
            transformation_summary=summary, created_at=_now_iso(),
        )
        db.collection("integration_jobs").document(job_id).set(job.model_dump())
        return _FileInterpretation(
            filename=filename, job_id=job_id, map_result=result, counts=summary.entity_counts,
        )
    except Exception as e:
        logger.exception("interpret_file failed: filename=%s error=%s", filename, e)
        return _FileInterpretation(filename=filename, error=str(e)[:500])


# ── 確定（conform）: マスタを実在検索 find-or-create で確定・永続 ────────────────

def _master_appeal_spec(kind: str, entity: Any) -> tuple[str, dict] | None:
    """マスタの appeal 生成スペック (summary_kind, payload)。account は appeal を持たない。"""
    if kind == "events":
        return "event", {
            "name": entity.name, "event_type": entity.event_type.value,
            "venue": entity.venue, "description": entity.description,
        }
    if kind == "products":
        return "product", {
            "product_name": entity.product_name, "product_category": entity.product_category,
        }
    if kind == "contents":
        return "content", {
            "content_name": entity.content_name, "content_type": entity.content_type.value,
            "description": entity.description,
        }
    return None


async def _appeal_or_empty(
    spec: tuple[str, dict] | None, space: Optional[SpaceContext]
) -> tuple[str, list[float]]:
    if not spec:
        return "", []
    return await semantic_search.build_appeal(spec[0], spec[1], space=space)


async def _conform_masters(
    db: Any,
    space: Optional[SpaceContext],
    interps: list[_FileInterpretation],
    default_event: str,
    resolvers: dict[str, EntityResolver],
) -> set[str]:
    """全ファイルのマスタ（events/accounts/products/contents）を確定・永続する。

    詳細レコードと観測からの参照を resolver で同一 UUID に畳み、payload を蓄積してから
    1 マスタ 1 回だけ永続＋appeal 生成する（表記揺れ・依存順に依らず収束）。

    Returns: このバッチで確定した event の uuid 集合（bind の単一イベントフォールバック判定に使う）。
    """
    space_id = space.space_id if space is not None else ""
    # masters[kind] = { uuid: payload }
    masters: dict[str, dict[str, dict]] = {
        "events": {}, "accounts": {}, "products": {}, "contents": {},
    }

    # 1) 詳細レコード（フルの payload）
    for itp in interps:
        if not itp.map_result:
            continue
        for rec in itp.map_result.records:
            if rec.kind in ("events", "accounts", "products"):
                uid, _ = resolvers[rec.kind].resolve(rec.name, display=rec.name)
                if uid:
                    masters[rec.kind].setdefault(uid, {}).update(rec.payload)
            elif rec.kind == "contents":
                uid, _ = resolvers["contents"].resolve(rec.name, display=rec.name)
                if not uid:
                    continue
                ev = rec.links.get("event", "")
                ev_id = resolvers["events"].resolve(ev, display=ev)[0] if ev else None
                payload = {**rec.payload}
                if ev_id:
                    payload["linked_event_id"] = ev_id
                masters["contents"].setdefault(uid, {}).update(payload)
            elif rec.kind == "event_patch":
                ev = rec.links.get("event", "")
                ev_id = resolvers["events"].resolve(ev, display=ev)[0] if ev else None
                if ev_id:
                    patch = {k: v for k, v in rec.payload.items() if v is not None}
                    masters["events"].setdefault(ev_id, {}).update(patch)

    # 2) 観測・費用からの参照（リンク名のみ）→ 最低限のマスタを保証（find-or-create の発見）
    def _ensure(kind: str, name: str, name_field: str) -> None:
        if not name:
            return
        uid, _ = resolvers[kind].resolve(name, display=name)
        if uid:
            masters[kind].setdefault(uid, {}).setdefault(name_field, name)

    for itp in interps:
        if not itp.map_result:
            continue
        for obs in itp.map_result.person_observations:
            _ensure("events", obs.event_link_name or default_event, "name")
            _ensure("accounts", obs.company_name, "account_name")
            for pn in obs.product_link_names:
                _ensure("products", pn, "product_name")
        for rec in itp.map_result.records:
            if rec.kind == "cost_items":
                _ensure("events", rec.links.get("event", ""), "name")

    # 3) エンティティ構築 → appeal 生成（並列）→ 永続
    builders = {
        "events": (Event, "event_id", "events"),
        "accounts": (Account, "account_id", "accounts"),
        "products": (Product, "product_id", "products"),
        "contents": (Content, "content_id", "contents"),
    }
    pending: list[tuple[str, str, Any, tuple[str, dict] | None]] = []
    for kind, items in masters.items():
        Model, id_field, collection = builders[kind]
        for uid, payload in items.items():
            try:
                entity = Model(**{id_field: uid, "space_id": space_id, **payload})
            except Exception:
                logger.exception("conform build failed: kind=%s uid=%s payload=%s",
                                 kind, uid, payload)
                continue
            pending.append((collection, uid, entity, _master_appeal_spec(kind, entity)))

    appeals = await asyncio.gather(*[_appeal_or_empty(spec, space) for *_, spec in pending])
    for (collection, uid, entity, spec), (summary, vector) in zip(pending, appeals):
        if spec:
            entity.appeal_summary = summary
            entity.appeal_vector = vector
        db.document(f"{collection}/{uid}").set(entity.model_dump(), merge=True)
    logger.info("conform done: %s", {k: len(v) for k, v in masters.items()})
    return set(masters["events"].keys())


# ── 結合（bind）: person を確定し、ファクトを確定 UUID へ束ねて永続 ──────────────

async def _bind_facts(
    db: Any,
    space: Optional[SpaceContext],
    interps: list[_FileInterpretation],
    resolvers: dict[str, EntityResolver],
    default_event: str,
    job_id: str,
    batch_event_ids: set[str] | None = None,
) -> set[str]:
    """person を find-or-create し、event_attendances / product_interests / cost を永続する。

    イベントリンクの解決順: 行の列/AI 既定（obs.event_link_name）→ 明示の既定イベント名
    （default_event）→ バッチが単一イベントのみのときのフォールバック。参加者ファイルに
    イベント列が無く（よくある）、かつそのイベントの概要等を同じバッチで取り込んだケースを救う。

    Returns: 触れた person_id 集合（appeal 導出の対象）。
    """
    space_id = space.space_id if space is not None else ""
    person_res = resolvers["persons"]
    # ファクトの冪等性: バッチ内の重複生成を (person,event,action) / (person,product) で抑止
    att_res = EntityResolver("attendance", [], "att_", fuzzy=False)
    int_res = EntityResolver("interest", [], "int_", fuzzy=False)
    touched: set[str] = set()
    # バッチに単一イベントしか無ければ、リンク未指定の観測をそのイベントへ束ねる
    solo_event_id = (
        next(iter(batch_event_ids)) if batch_event_ids and len(batch_event_ids) == 1 else None
    )
    fallback_used = 0

    for itp in interps:
        if not itp.map_result:
            continue
        for obs in itp.map_result.person_observations:
            now = _now_iso()
            account_id = None
            if obs.company_name:
                account_id = resolvers["accounts"].resolve(obs.company_name)[0]

            pkey = _person_key(obs.name, obs.email, obs.company_name)
            pid, _created = person_res.resolve(pkey, display=obs.name)
            if not pid:
                continue
            person = Person(
                person_id=pid, space_id=space_id, account_id=account_id,
                name=obs.name, email=obs.email or None, department=obs.department,
                job_title=obs.job_title, stage=ContactStage.LEAD,
                engagement_level=obs.engagement_level, source_job_id=job_id, created_at=now,
            )
            # appeal_* は導出ステージで全 attendance を集約して付与する（ここでは温存）
            db.document(f"persons/{pid}").set(
                person.model_dump(exclude={"appeal_summary", "appeal_vector"}), merge=True)
            touched.add(pid)

            # 参加ファクト（接客事実つき）
            ev_name = obs.event_link_name or default_event
            ev_id = resolvers["events"].resolve(ev_name)[0] if ev_name else None
            if not ev_id and solo_event_id:
                ev_id = solo_event_id  # 単一イベントバッチのフォールバック
                fallback_used += 1
            if ev_id:
                aid, _ac = att_res.resolve(f"{pid}|{ev_id}|{obs.action_type}")
                att = EventAttendance(
                    attendance_id=aid, space_id=space_id, person_id=pid, event_id=ev_id,
                    action_type=obs.action_type, owner_staff=obs.owner_staff,
                    challenge_note=obs.challenge_note, memo=obs.memo,
                    source_job_id=job_id, created_at=now,
                )
                db.document(f"event_attendances/{aid}").set(att.model_dump(), merge=True)

            # 製品関心ファクト
            for pn in obs.product_link_names:
                pr_id = resolvers["products"].resolve(pn)[0]
                if not pr_id:
                    continue
                iid, _ic = int_res.resolve(f"{pid}|{pr_id}")
                pi = ProductInterest(
                    interest_id=iid, space_id=space_id, person_id=pid, product_id=pr_id,
                    source_job_id=job_id, created_at=now,
                )
                db.document(f"product_interests/{iid}").set(pi.model_dump(), merge=True)

        # 費用ファクト（event 解決）
        for rec in itp.map_result.records:
            if rec.kind != "cost_items":
                continue
            ev = rec.links.get("event", "")
            ev_id = resolvers["events"].resolve(ev)[0] if ev else None
            if not ev_id:
                continue
            cost_id = _new_id("cost_")
            try:
                cost = CostItem(cost_id=cost_id, event_id=ev_id, **rec.payload)
            except Exception:
                logger.exception("cost build failed: payload=%s", rec.payload)
                continue
            db.document(f"events/{ev_id}/costs/{cost_id}").set(cost.model_dump(), merge=True)

    logger.info("bind done: touched_persons=%d solo_event_fallback=%d", len(touched), fallback_used)
    return touched


# ── 導出（derive）: person.appeal を全 attendance から集約再生成 ──────────────────

def _person_appeal_payload(
    person: dict, encounters: list[dict], interests: list[str]
) -> dict:
    enc_lines: list[str] = []
    for e in encounters:
        parts = [
            f"イベント: {e['event']}" if e.get("event") else "",
            f"日時: {e['date']}" if e.get("date") else "",
            f"接客担当: {e['owner_staff']}" if e.get("owner_staff") else "",
            f"課題感: {e['challenge_note']}" if e.get("challenge_note") else "",
            f"メモ: {e['memo']}" if e.get("memo") else "",
        ]
        line = " / ".join(p for p in parts if p)
        if line:
            enc_lines.append(line)
    return {
        "name": person.get("name", ""),
        "department": person.get("department", ""),
        "job_title": person.get("job_title", ""),
        "engagement_level": person.get("engagement_level") or "",
        "接客履歴": "\n".join(enc_lines),
        "関心製品": ", ".join(interests),
    }


async def _derive_person_appeal(
    db: Any, space: Optional[SpaceContext], person_ids: set[str]
) -> None:
    """touched person の appeal_summary / appeal_vector を全 attendance から集約再生成する。"""
    if not person_ids:
        return

    events_map: dict[str, dict] = {}
    products_map: dict[str, dict] = {}
    try:
        for doc in db.collection("events").get():
            d = doc.to_dict() or {}
            events_map[d.get("event_id") or doc.id] = d
    except Exception:
        logger.exception("derive: load events failed")
    try:
        for doc in db.collection("products").get():
            d = doc.to_dict() or {}
            products_map[d.get("product_id") or doc.id] = d
    except Exception:
        logger.exception("derive: load products failed")

    sem = asyncio.Semaphore(8)

    async def _one(pid: str) -> None:
        async with sem:
            pdoc = db.document(f"persons/{pid}").get()
            if not getattr(pdoc, "exists", False):
                return
            person = pdoc.to_dict() or {}

            encounters: list[dict] = []
            try:
                atts = db.collection("event_attendances").where("person_id", "==", pid).get()
            except Exception:
                atts = []
            for a in atts:
                ad = a.to_dict() or {}
                ev = events_map.get(ad.get("event_id"), {})
                encounters.append({
                    "event": ev.get("name", ""),
                    "date": ev.get("event_date", ""),
                    "owner_staff": ad.get("owner_staff", ""),
                    "challenge_note": ad.get("challenge_note", ""),
                    "memo": ad.get("memo", ""),
                })

            interests: list[str] = []
            try:
                pis = db.collection("product_interests").where("person_id", "==", pid).get()
            except Exception:
                pis = []
            for pi in pis:
                pd = pi.to_dict() or {}
                pr = products_map.get(pd.get("product_id"), {})
                if pr.get("product_name"):
                    interests.append(pr["product_name"])

            payload = _person_appeal_payload(person, encounters, interests)
            summary, vector = await semantic_search.build_appeal("person", payload, space=space)
            db.document(f"persons/{pid}").set(
                {"appeal_summary": summary, "appeal_vector": vector}, merge=True)

    await asyncio.gather(*[_one(pid) for pid in person_ids])
    logger.info("derive done: persons=%d", len(person_ids))


# ── バッチ処理（依存順の多段オーケストレーション）─────────────────────────────────

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
    event: str | None = None,
) -> list[BatchFileResult]:
    """複数ファイルを依存順の多段で統合する（ADR-011）。

    観測着地+解釈（並列）→ 確定(conform: マスタ)→ 結合(bind: person＋ファクト)→
    導出(derive: person.appeal の集約再生成)。

    hint はユーザーの自然言語ヒント。event は明示的な既定イベント名（hint より強いシグナル）。
    space は LLM トークン計測に使う（None なら計測しない）。
    """
    default_event = (event or "").strip()

    # 1) 観測着地 + 解釈（ファイル並列）
    interps = list(await asyncio.gather(
        *[_interpret_file(fn, content, hint, db, space=space) for fn, content in files]
    ))

    # resolver を既存マスタからシード
    resolvers = {
        "events": EntityResolver("event", _load_existing(db, "events", "name", "event_id"), "event_"),
        "accounts": EntityResolver(
            "account", _load_existing(db, "accounts", "account_name", "account_id"), "account_"),
        "products": EntityResolver(
            "product", _load_existing(db, "products", "product_name", "product_id"), "product_"),
        "contents": EntityResolver(
            "content", _load_existing(db, "contents", "content_name", "content_id"), "content_"),
        "persons": EntityResolver("person", _load_existing_persons(db), "person_", fuzzy=False),
    }

    job_id = _new_id("job_")  # conform/bind の由来として facts に刻む

    # 2) 確定（conform）→ 3) 結合（bind）→ 4) 導出（derive）
    batch_event_ids = await _conform_masters(db, space, interps, default_event, resolvers)
    touched = await _bind_facts(
        db, space, interps, resolvers, default_event, job_id, batch_event_ids=batch_event_ids)
    await _derive_person_appeal(db, space, touched)

    # 解決・新規採番したリンク先マスタをバッチ doc に監査記録
    resolved_links: list[dict] = []
    for r in resolvers.values():
        resolved_links.extend(r.log)
    try:
        db.document(f"integration_jobs/{batch_id}").set(
            {"resolved_links": resolved_links}, merge=True)
    except Exception:
        logger.exception("write resolved_links failed: batch_id=%s", batch_id)

    return [
        BatchFileResult(filename=itp.filename, status="error", error=itp.error)
        if itp.error else
        BatchFileResult(
            filename=itp.filename, status="done", job_id=itp.job_id, created_entities=itp.counts)
        for itp in interps
    ]
