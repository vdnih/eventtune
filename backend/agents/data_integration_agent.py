"""
DataIntegrationAgent — Layer 1: データ統合パイプライン

入力ファイルの種別に応じて2つの処理パスを実行する:
  パス A (表形式: CSV/Excel): SchemaMapper でカラムマッピングを生成 → OntologyMapper で行変換
  パス B (非構造化: TXT):     DocumentExtractor でエンティティ抽出 → OntologyMapper で変換

どちらのパスも OntologyMapper を通すことで、EngagementLevel 判定などの
ビジネスロジックを AI から切り離し、決定論的に実行する。
"""

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

from agents.ontology_mapper import OntologyMapper
from ontology import (
    ColumnMappingResult,
    Contact,
    ContentAsset,
    CostItem,
    DataLineage,
    DocumentExtractionResult,
    Event,
    EventKPI,
    SurveyResponse,
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


# ── パスA: SchemaMapper ──────────────────────────────────────────────────────
#
# column_map はキーがCSVカラム名で可変のため、response_schema（controlled generation）
# では空 dict になってしまう。そのため response_schema は使わず JSONモードのみで
# 自由形式 JSON を受け取り、Python でパースする。

_SCHEMA_MAPPER_PROMPT = """\
あなたはデータ統合の専門家です。
以下のCSVカラムヘッダーとサンプルデータを読んで、
このテーブルがどのエンティティを表しているか判断し、
各カラムをオントロジーフィールドにマッピングしてください。

【オントロジーのエンティティとフィールド】

contacts (Contact エンティティ):
  - name_last: 姓
  - name_first: 名
  - company_name: 会社名
  - department: 部署名
  - job_title: 役職
  - email: メールアドレス
  - extracted_challenge: 課題・悩み
  - __engagement_signal: 判定ランク (A/B/C)。EngagementLevel の判定に使用
  - __temperature_signal: 温度感 (ホット/ウォーム/コールド/高/中/低)。EngagementLevel の判定に使用
  - __product_signal: 関心サービス・製品名。Product の名寄せに使用
  - __memo: 接客内容メモ・所感 → notes フィールドへ集約
  - __needs: お客様の要望・ニーズ → notes フィールドへ集約
  - __caution: 注意事項 → notes フィールドへ集約

cost_items (CostItem エンティティ):
  - category: 費用カテゴリ
  - description: 費用の説明
  - amount_jpy: 金額（円）
  - vendor_name: 業者名
  - invoice_date: 請求書日付

【ルール】
- entity_type は "contacts" または "cost_items" を返す
- column_map にすべてのカラムを含める（マッピングできないものは unmapped_columns へ）
- contacts 判断基準: 氏名・会社名・部署などの人物情報が含まれている
- cost_items 判断基準: 金額・費用項目などの経費情報が含まれている
- __ プレフィックスのフィールドは OntologyMapper でさらに変換される特殊フィールド

【出力形式】
次の形の JSON オブジェクトのみを返してください（説明文やコードフェンスは不要）:
{
  "entity_type": "contacts" または "cost_items",
  "column_map": { "CSVカラム名": "オントロジーフィールド名", ... },
  "unmapped_columns": [ "マッピングできなかったCSVカラム名", ... ]
}
"""


def _parse_json_response(text: str) -> dict:
    """JSONモードのレスポンス文字列を dict にパースする。
    コードフェンスや前後ノイズが混じっても落ちないよう防御的に処理する。"""
    text = text.strip()
    if text.startswith("```"):
        # ```json ... ``` のフェンスを除去
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # オブジェクト本体だけを抽出して再試行
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        result = json.loads(text[start : end + 1])
    return result if isinstance(result, dict) else {}


async def run_schema_mapper(
    headers: list[str], sample_rows: list[dict]
) -> ColumnMappingResult:
    sample_text = json.dumps(sample_rows[:5], ensure_ascii=False, indent=2)
    prompt = (
        f"{_SCHEMA_MAPPER_PROMPT}\n\n"
        f"【カラムヘッダー】\n{headers}\n\n"
        f"【サンプルデータ（先頭5行）】\n{sample_text}"
    )
    # column_map のキーが可変のため response_schema は使わず JSONモードのみ
    response = await _get_client().aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    parsed = _parse_json_response(response.text)
    raw_map = parsed.get("column_map") or {}
    # キー・値とも str に正規化（防御的）
    column_map = {str(k): str(v) for k, v in raw_map.items() if v}
    unmapped = parsed.get("unmapped_columns") or []
    return ColumnMappingResult(
        entity_type=str(parsed.get("entity_type", "")),
        column_map=column_map,
        unmapped_columns=[str(c) for c in unmapped],
    )


# ── パスB: DocumentExtractor ─────────────────────────────────────────────────
#
# 自由 dict は controlled generation で空になるため、固定キーの具体スキーマで定義する。
# フィールドは ontology の最終型に近い型（AI が表記正規化まで行う前提）。
# 全フィールド Optional・ID/タイムスタンプ無し・enum は表記揃えのため str
# （最終的な enum 化・判定・名寄せは OntologyMapper が決定論的に行う）。

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
    event: Optional[_EventExtraction] = None
    event_kpi: Optional[_EventKPIExtraction] = None
    cost_items: Optional[list[_CostItemExtraction]] = None
    survey_response: Optional[_SurveyResponseExtraction] = None
    content_assets: Optional[list[_ContentAssetExtraction]] = None


_DOCUMENT_EXTRACTOR_PROMPT = """\
あなたはイベントマーケティングデータの統合専門家です。
以下のドキュメントを読み、含まれる情報をオントロジーのエンティティに変換してください。
1つのドキュメントから複数のエンティティが抽出されることがあります。

【オントロジーのエンティティとフィールド】

Event:
  name, event_type（展示会/セミナー/プライベートイベント）, status（計画中/開催中/終了）,
  venue, event_date（YYYY-MM-DD）, event_date_end（YYYY-MM-DD）, booth_number,
  total_budget（数値のみ、円記号・カンマ除去）, target_contact_count（数値）,
  description（所感・目的・担当者メモなど、構造化できない文脈情報をすべてここに）

EventKPI:
  total_visitors_to_booth, total_contacts_collected, appointments_booked, demo_sessions_held,
  follow_email_open_rate（0.0〜1.0の小数。61%→0.61）,
  follow_email_reply_rate（0.0〜1.0の小数）,
  pipeline_value_jpy（数値のみ）, closed_deals_3m, closed_revenue_3m_jpy（数値のみ）,
  contacts_by_engagement: { appointment_booked: 数値, high_intent: 数値, nurturing: 数値 }

CostItem（複数可）:
  category（ブース出展料/ブース装飾・設営/機材・備品/人件費・派遣/交通・宿泊/印刷・販促物/飲食・接待/その他）,
  description（費用の説明）, amount_jpy（数値のみ）, vendor_name, invoice_date（YYYY-MM-DD）

SurveyResponse:
  total_responses, nps_score（-100〜100の小数）, nps_promoters, nps_passives, nps_detractors,
  satisfaction_scores: [{ category: カテゴリ名, avg_score: 小数, response_count: 数値 }],
  verbatim_positives: [コメント文字列], verbatim_negatives: [コメント文字列],
  verbatim_suggestions: [コメント文字列]

ContentAsset（複数可）:
  asset_id, content_type（未来のセミナー（募集中）/未来のイベント（募集中）/資料・ホワイトペーパー/導入事例）,
  name, description, url, linked_event_id

【ルール】
- ドキュメントに含まれるエンティティのみ detected_entity_types に列挙する
- 数値フィールドのカンマ・通貨記号・単位を除去する
- パーセント表記（61%）は小数（0.61）に変換する
- 構造化できない文脈・所感・メモは Event.description に集約する
- 含まれない情報は null にする（推測で埋めない）
"""


async def run_document_extractor(text: str) -> DocumentExtractionResult:
    prompt = f"{_DOCUMENT_EXTRACTOR_PROMPT}\n\n【ドキュメント】\n{text}"
    response = await _get_client().aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_DocumentExtractionResponse,
        ),
    )
    parsed = _DocumentExtractionResponse.model_validate_json(response.text)
    # 具体スキーマ → dict 境界（DocumentExtractionResult）へ詰め直す。
    # 以降の OntologyMapper / DataLineage は従来通り dict を受ける。
    return DocumentExtractionResult(
        detected_entity_types=parsed.detected_entity_types,
        event=parsed.event.model_dump() if parsed.event else None,
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

def _entity_to_firestore_path(
    entity: Any, event_id: str | None, batch_id: str | None
) -> tuple[str | None, str | None]:
    if isinstance(entity, Contact):
        eid = entity.source_event_id or event_id or "unknown"
        bid = batch_id or "unknown"
        return f"events/{eid}/batches/{bid}/contacts", entity.contact_id
    if isinstance(entity, Event):
        return "events", entity.event_id
    if isinstance(entity, EventKPI):
        return f"events/{entity.event_id}/kpi", entity.kpi_id
    if isinstance(entity, CostItem):
        return f"events/{entity.event_id}/costs", entity.cost_id
    if isinstance(entity, SurveyResponse):
        return f"events/{entity.event_id}/survey", entity.survey_id
    if isinstance(entity, ContentAsset):
        return "content_assets", entity.asset_id
    return None, None


# ── メイン処理 ────────────────────────────────────────────────────────────────

async def process_file(
    filename: str,
    content: bytes,
    event_id: str | None,
    batch_id: str,
    db: Any,  # firebase_admin.firestore.Client
) -> tuple[list[Any], DataLineage]:
    """
    1ファイルを処理してオントロジーエンティティを生成し、Firestore に保存する。

    Returns:
        (entities, lineage): 生成されたエンティティのリストと DataLineage レコード
    """
    logger.info("process_file: filename=%s batch_id=%s event_id=%s", filename, batch_id, event_id)

    entities: list[Any] = []
    transformations: list[Any] = []
    skipped: list[Any] = []
    column_mapping = None
    raw_extraction_dict = None

    if _is_tabular(filename):
        # ─ パス A: CSV / Excel ───────────────────────────────────────────
        headers, rows = _read_tabular(filename, content)
        column_mapping = await run_schema_mapper(headers, rows)
        logger.info("schema_mapper result: entity_type=%s columns=%d", column_mapping.entity_type, len(column_mapping.column_map))
        entities, transformations, skipped = _mapper.map_rows(
            column_mapping, rows, event_id=event_id, batch_id=batch_id
        )
    else:
        # ─ パス B: テキスト / その他 ─────────────────────────────────────
        text = _read_text(content)
        extraction = await run_document_extractor(text)
        raw_extraction_dict = extraction.model_dump()
        logger.info("document_extractor result: detected=%s", extraction.detected_entity_types)
        entities, transformations, skipped = _mapper.map_extraction(
            extraction, event_id=event_id, batch_id=batch_id
        )

    # Firestore に書き込み
    created_ids: dict[str, list[str]] = {}
    # Contact が入った events/{eid}/batches/{bid} の親ドキュメントを実体化するため、
    # コンタクトが書き込まれた event_id を記録する。
    contact_event_ids: set[str] = set()
    for entity in entities:
        collection, doc_id = _entity_to_firestore_path(entity, event_id, batch_id)
        if collection and doc_id:
            db.document(collection + "/" + doc_id).set(entity.model_dump(), merge=True)
            key = type(entity).__name__
            created_ids.setdefault(key, []).append(doc_id)
            if isinstance(entity, Contact):
                contact_event_ids.add(entity.source_event_id or event_id or "unknown")

    # batches/{batch_id} ドキュメントを明示的に作成する。
    # これを書かないと中間ドキュメントが「幽霊（サブコレクションのみの祖先）」となり、
    # コレクションクエリ collection(".../batches").get() でバッチを列挙できなくなる。
    for eid in contact_event_ids:
        db.document(f"events/{eid}/batches/{batch_id}").set(
            {"batch_id": batch_id, "event_id": eid}, merge=True
        )

    logger.info("process_file done: created=%s", {k: len(v) for k, v in created_ids.items()})

    # ステージ2（加工処理）のサマリを集計
    summary = _summarize_transformations(entities, transformations, skipped)

    # DataLineage レコードを保存
    lineage = DataLineage(
        lineage_id=_new_id("lineage_"),
        source_filename=filename,
        source_type="tabular" if _is_tabular(filename) else "unstructured",
        batch_id=batch_id,
        column_mapping=column_mapping,
        raw_extraction=raw_extraction_dict,
        created_entity_ids=created_ids,
        transformations=transformations,
        skipped_records=skipped,
        transformation_summary=summary,
        created_at=_now_iso(),
    )
    db.collection("data_lineage").document(lineage.lineage_id).set(lineage.model_dump())

    return entities, lineage


def _summarize_transformations(
    entities: list[Any],
    transformations: list[Any],
    skipped: list[Any],
) -> TransformationSummary:
    """ステージ2の加工結果からバッチ単位のサマリを集計する。"""
    entity_counts: dict[str, int] = {}
    engagement_breakdown: dict[str, int] = {}
    product_breakdown: dict[str, int] = {}

    for entity in entities:
        entity_counts[type(entity).__name__] = entity_counts.get(type(entity).__name__, 0) + 1
        if isinstance(entity, Contact):
            if entity.engagement_level:
                lvl = entity.engagement_level.value
                engagement_breakdown[lvl] = engagement_breakdown.get(lvl, 0) + 1
            for product in entity.interested_products:
                product_breakdown[product.value] = product_breakdown.get(product.value, 0) + 1

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
    lineage_id: str | None = None
    created_entities: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    role: str = ""  # "event_defining" | "dependent_doc" | "tabular"
    generated_event_id: str | None = None  # このファイルが生成した Event の id（横断伝播用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "status": self.status,
            "lineage_id": self.lineage_id,
            "created_entities": self.created_entities,
            "error": self.error,
            "role": self.role,
        }


def _resolve_event_id(explicit: str | None, batch_resolved: str | None) -> str | None:
    """バッチ内で使う event_id を優先順位に従って決定する（決定論的）。

    優先順位:
      1. UI で明示選択された event_id（既存イベントへの追加取り込み）
      2. バッチ内ドキュメントから生成された Event の event_id（overview.txt 由来）
      3. None → 既存フォールバック（Contact は events/unknown/... に入る）
    """
    return explicit or batch_resolved or None


def _is_event_defining_name(filename: str) -> bool:
    """ファイル名から Event を定義しうるドキュメントかを推定する（決定論ヒューリスティック）。

    overview / 概要 を含む非表形式ファイルを先に処理することで、
    survey 等の依存ドキュメントが処理される前に event_id を確定させる。
    """
    lower = filename.lower()
    return "overview" in lower or "概要" in filename


async def process_batch(
    files: list[tuple[str, bytes]],
    event_id: str | None,
    batch_id: str,
    db: Any,  # firebase_admin.firestore.Client
) -> tuple[list[BatchFileResult], str | None]:
    """複数ファイルを1バッチとして横断的に処理する。

    同一イベントに属するファイル群（例: leads.csv + overview.txt + survey.txt）を
    まとめて取り込み、overview.txt から確定する event_id を leads/survey に
    横断伝播させる。処理順と event_id 伝播は AI を使わず決定論的に行う（原則4）。

    Returns:
        (per-file 結果リスト, バッチで確定した resolved_event_id)
    """
    # 表形式 / 非表形式に分け、非表形式は Event 定義候補（overview/概要）を先頭へ寄せる
    non_tabular = [f for f in files if not _is_tabular(f[0])]
    tabular = [f for f in files if _is_tabular(f[0])]
    non_tabular.sort(key=lambda f: 0 if _is_event_defining_name(f[0]) else 1)

    results: list[BatchFileResult] = []
    batch_event_id: str | None = None

    # ─ パス1: 非表形式（Event を生成しうる）を先に処理 ─────────────────────────
    for filename, content in non_tabular:
        current_event_id = _resolve_event_id(event_id, batch_event_id)
        result = await _process_one(filename, content, current_event_id, batch_id, db)
        # 生成エンティティに Event が含まれ、まだ未確定なら採用
        if result.status == "done" and batch_event_id is None and event_id is None:
            if result.generated_event_id:
                batch_event_id = result.generated_event_id
        result.role = "event_defining" if _is_event_defining_name(filename) else "dependent_doc"
        results.append(result)

    resolved = _resolve_event_id(event_id, batch_event_id)

    # ─ パス2: 表形式（resolved event_id を伝播）─────────────────────────────────
    for filename, content in tabular:
        result = await _process_one(filename, content, resolved, batch_id, db)
        result.role = "tabular"
        results.append(result)

    return results, resolved


async def _process_one(
    filename: str,
    content: bytes,
    event_id: str | None,
    batch_id: str,
    db: Any,
) -> BatchFileResult:
    """1ファイルを process_file で処理し、部分失敗を吸収して結果を返す。"""
    try:
        entities, lineage = await process_file(filename, content, event_id, batch_id, db)
        counts: dict[str, int] = {}
        for entity in entities:
            counts[type(entity).__name__] = counts.get(type(entity).__name__, 0) + 1
        # 生成された Event の id を横断伝播のために控える
        ev = next((e for e in entities if isinstance(e, Event)), None)
        return BatchFileResult(
            filename=filename,
            status="done",
            lineage_id=lineage.lineage_id,
            created_entities=counts,
            generated_event_id=ev.event_id if ev is not None else None,
        )
    except Exception as e:
        logger.exception("process_one failed: filename=%s error=%s", filename, e)
        return BatchFileResult(
            filename=filename,
            status="error",
            error=str(e)[:500],
        )
