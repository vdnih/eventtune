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
from config import get_settings
from metering import record_llm_response
from space import SpaceContext
from ontology import (
    Account,
    ContactStage,
    Content,
    CostItem,
    DocumentExtractionResult,
    DocumentPlan,
    Event,
    EventAttendance,
    IntegrationJob,
    Person,
    Product,
    ProductInterest,
    TransformationSummary,
)

logger = logging.getLogger(__name__)

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


# ── パスA: バッチ横断理解 + 行単位並列抽出（ADR-013）────────────────────────────



_ONTOLOGY_DEFINITION = """\
【オントロジー定義（エンティティ間の関係・各フィールドの業務的意味）】
OSI セマンティックレイヤー: 5マスタ（persons/accounts/events/products/contents）+ 3ファクト

persons（人物マスタ）: name / email / company_name / department / job_title
  ※ 感度・ステータス等の業務的判定は行わない。観測事実のみ格納。

event_attendances（接客ファクト / persons×events）:
  action_type（参加/商談/問合せ等） / owner_staff（接客担当者名）
  challenge_note（接客時に把握した課題感。担当者主観の興味レベルもここに含める）
  memo（その他メモ・所感）
  ※ challenge_note には「感度A」「関心高め」等のテキストもそのまま含める（分類しない）

product_interests（製品関心ファクト / persons×products）

cost_items（費用ファクト / events の費用明細。展示会・セミナー共通）:
  category（会場費・出展費/ブース装飾・設営/集客/登壇者/人件費・派遣/交通・宿泊/印刷・販促物・ノベルティ/運営/飲食・接待/その他）
  description / amount_jpy（数値のみ）/ vendor_name / invoice_date（YYYY-MM-DD）
  ※ 必ず特定の event にリンクされる。link_hints に {"event": "イベント名"} を設定する。

events（イベントマスタ）: name / event_type / event_date / venue / description 等
accounts（企業マスタ）: account_name / industry_type / company_size
products（製品マスタ）: product_name / product_category
contents（コンテンツマスタ）: content_name / content_type / url
"""

_BATCH_UNDERSTAND_PROMPT = """\
あなたは EventTune のイベントマーケティングデータ統合の専門家です。
バッチ内の全ファイルのヘッダーとサンプル行を読み、各ファイルの業務的な役割・内容・相互関係を把握して
1ファイルごとの DocumentPlan を生成してください。

{ontology}

【ルール】
- entity_type: persons / events / accounts / products / contents / cost_items のいずれか
- link_hints: このファイルに含まれるデータが関連するマスタ名 {{"event": "展示会名"}} 等
  （バッチ内の別ファイル（イベント概要等）から推定できる場合は積極的に設定する）
- column_map: CSVカラム名 → オントロジーフィールド名のマッピング
  persons の場合のフィールド: name/name_last/name_first/email/company_name/department/job_title
    接客事実: owner_staff/challenge_note/memo/action_type
    製品リンク: product_link_names（複数可）
- unmapped_notes: マッピングできなかったカラムや不明な点の説明
- source_file_role: participant_list / event_master / cost_list / content_list 等

【出力形式】ファイル名をキーとした JSON オブジェクト:
{{
  "ファイル名.csv": {{
    "business_context": "...",
    "entity_type": "...",
    "source_file_role": "...",
    "link_hints": {{"event": "..."}},
    "column_map": {{"CSVカラム名": "オントロジーフィールド名"}},
    "unmapped_notes": "..."
  }},
  ...
}}
"""

_ROW_EXTRACT_PROMPT = """\
あなたは EventTune のイベントマーケティングデータ統合の専門家です。
1件の接客記録（CSVの1行）を構造化データへ変換してください。

{ontology_short}

【このファイルの業務文脈】
{business_context}
エンティティ種別: {entity_type}
リンク先: {link_hints}
カラムマッピング: {column_map}

【変換対象の行データ】
{row_json}

【ルール】
- challenge_note には担当者主観の興味度（「感度A」「関心高め」等）もテキストのまま含める
- product_link_names には関連製品名をリストで（複数可）
- event_link_name はリンク先イベント名（行データに無ければ link_hints から補完）
- 名前が空の場合は skip_reason を設定する
- 推測で値を埋めない
"""

_ONTOLOGY_SHORT = """\
persons フィールド: name/email/company_name/department/job_title/owner_staff/challenge_note/memo/action_type/product_link_names/event_link_name
"""


class _PersonObservationExtraction(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    company_name: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    owner_staff: Optional[str] = None
    challenge_note: Optional[str] = None
    memo: Optional[str] = None
    action_type: Optional[str] = None
    product_link_names: list[str] = []
    event_link_name: Optional[str] = None
    skip_reason: Optional[str] = None


async def understand_batch(
    files: list[tuple[str, bytes]],
    hint: str | None,
    space: Optional[SpaceContext] = None,
) -> dict[str, DocumentPlan]:
    """バッチ内全ファイルのヘッダー+サンプルをフルモデルに渡し、各ファイルの DocumentPlan を生成する。"""
    if not files:
        return {}

    files_block_parts: list[str] = []
    for filename, content in files:
        if _is_tabular(filename):
            try:
                headers, rows = _read_tabular(filename, content)
                sample = json.dumps(rows[:5], ensure_ascii=False)
                files_block_parts.append(
                    f"--- {filename} ---\nヘッダー: {headers}\nサンプル: {sample}"
                )
            except Exception as e:
                files_block_parts.append(f"--- {filename} ---\n読み込みエラー: {e}")
        else:
            try:
                text = _read_text(content)
                files_block_parts.append(f"--- {filename} ---\n{text[:800]}")
            except Exception:
                pass

    if not files_block_parts:
        return {}

    prompt = (
        _BATCH_UNDERSTAND_PROMPT.format(ontology=_ONTOLOGY_DEFINITION)
        + _hint_block(hint)
        + "\n\n【バッチ内ファイル一覧】\n"
        + "\n\n".join(files_block_parts)
    )
    try:
        _model = get_settings().model_ingestion
        response = await _get_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        if space is not None:
            record_llm_response(space, _model, response)
        raw = json.loads(response.text)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, DocumentPlan] = {}
        for fname, plan_dict in raw.items():
            if isinstance(plan_dict, dict):
                result[fname] = DocumentPlan(
                    business_context=str(plan_dict.get("business_context", "")),
                    entity_type=str(plan_dict.get("entity_type", "")),
                    source_file_role=str(plan_dict.get("source_file_role", "")),
                    link_hints={str(k): str(v) for k, v in (plan_dict.get("link_hints") or {}).items()},
                    column_map={str(k): str(v) for k, v in (plan_dict.get("column_map") or {}).items()},
                    unmapped_notes=str(plan_dict.get("unmapped_notes", "")),
                )
        logger.info("understand_batch: files=%s plans=%s", [f for f, _ in files], list(result.keys()))
        return result
    except Exception:
        logger.exception("understand_batch failed")
        return {}


async def _extract_single_row(
    row: dict, plan: DocumentPlan, space: Optional[SpaceContext] = None
) -> "PersonObservation | None":
    """1行の CSV データから PersonObservation を軽量モデルで抽出する。"""
    from agents.ontology_mapper import PersonObservation

    prompt = _ROW_EXTRACT_PROMPT.format(
        ontology_short=_ONTOLOGY_SHORT,
        business_context=plan.business_context or "(不明)",
        entity_type=plan.entity_type,
        link_hints=json.dumps(plan.link_hints, ensure_ascii=False),
        column_map=json.dumps(plan.column_map, ensure_ascii=False),
        row_json=json.dumps(row, ensure_ascii=False),
    )
    try:
        _model = get_settings().model_batch
        response = await _get_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PersonObservationExtraction,
            ),
        )
        if space is not None:
            record_llm_response(space, _model, response)
        ext = _PersonObservationExtraction.model_validate_json(response.text)
        if ext.skip_reason or not (ext.name or "").strip():
            return None
        return PersonObservation(
            name=(ext.name or "").strip(),
            email=(ext.email or "").strip(),
            company_name=(ext.company_name or "").strip(),
            department=(ext.department or "").strip(),
            job_title=(ext.job_title or "").strip(),
            event_link_name=(ext.event_link_name or "").strip(),
            action_type=(ext.action_type or "参加").strip(),
            product_link_names=ext.product_link_names or [],
            owner_staff=(ext.owner_staff or "").strip(),
            challenge_note=(ext.challenge_note or "").strip(),
            memo=(ext.memo or "").strip(),
            source_label=(ext.name or "").strip(),
        )
    except Exception:
        logger.exception("_extract_single_row failed: row=%s", str(row)[:200])
        return None


async def _extract_rows_parallel(
    filename: str,
    rows: list[dict],
    plan: DocumentPlan,
    space: Optional[SpaceContext] = None,
    job_id: str = "",
) -> "MapResult":
    """全行を並列で AI 抽出し MapResult へまとめる。"""
    from agents.ontology_mapper import MapResult, PersonObservation, SkippedRecord

    sem = asyncio.Semaphore(20)

    async def _bounded(row: dict) -> "PersonObservation | None":
        async with sem:
            return await _extract_single_row(row, plan, space)

    results = await asyncio.gather(*[_bounded(row) for row in rows])
    mr = MapResult()
    for obs, row in zip(results, rows):
        if obs is not None:
            mr.person_observations.append(obs)
        else:
            mr.skipped.append(
                SkippedRecord(entity_type="Person", reason="AI抽出スキップ",
                              detail=str(row)[:200])
            )
    logger.info("_extract_rows_parallel: file=%s rows=%d persons=%d skipped=%d",
                filename, len(rows), len(mr.person_observations), len(mr.skipped))
    return mr


# ── パスB: DocumentExtractor ─────────────────────────────────────────────────

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
あなたは EventTune のイベントマーケティングデータの統合専門家です。
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
  category（会場費・出展費/ブース装飾・設営/集客/登壇者/人件費・派遣/交通・宿泊/印刷・販促物・ノベルティ/運営/飲食・接待/その他）,
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
    _model = get_settings().model_ingestion
    response = await _get_client().aio.models.generate_content(
        model=_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_DocumentExtractionResponse,
        ),
    )
    if space is not None:
        record_llm_response(space, _model, response)
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


def _extract_cost_rows_from_csv(rows: list[dict], plan: DocumentPlan) -> MapResult:
    """費用CSVを column_map で決定論的に InterpretedRecord へ変換する（AI呼び出しなし）。

    ADR-013: AI は構造を解釈（understand_batch）、Python は決定論的にマップ。
    OntologyMapper._build_cost_item を再利用するため正規化ロジックの重複なし。
    """
    from agents.ontology_mapper import MapResult

    result = MapResult()
    col_map = plan.column_map
    event_link = plan.link_hints.get("event", "")

    for row in rows:
        mapped: dict = {}
        for csv_col, onto_field in col_map.items():
            if csv_col in row:
                mapped[onto_field] = row[csv_col]
        rec, skip = _mapper._build_cost_item(mapped, event_name=event_link)
        if rec is not None:
            result.records.append(rec)
            if rec.transform is not None:
                result.transformations.append(rec.transform)
        if skip is not None:
            result.skipped.append(skip)
    return result


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
    if result.person_observations:
        entity_counts["Person"] = len(result.person_observations)
    for rec in result.records:
        label = _KIND_LABEL.get(rec.kind, rec.kind)
        entity_counts[label] = entity_counts.get(label, 0) + 1
    return TransformationSummary(
        entity_counts=entity_counts,
        product_breakdown={},
        skipped_count=len(result.skipped),
    )


async def _interpret_file(
    filename: str,
    content: bytes,
    hint: str | None,
    db: Any,
    space: Optional[SpaceContext] = None,
    document_plan: Optional[DocumentPlan] = None,
) -> _FileInterpretation:
    """1ファイルを読み・AI 解釈し、中間レコード（MapResult）を返す。永続しない。

    CSVパス: document_plan（understand_batch 出力）を受け取り、行単位並列 AI 抽出を行う。
    TXTパス: DocumentExtractor で直接エンティティを抽出する。
    解釈の監査（column_mapping / raw_extraction）は integration_jobs に子ジョブとして残す。
    """
    job_id = _new_id("job_")
    space_id = space.space_id if space is not None else ""
    try:
        column_mapping: Optional[DocumentPlan] = None
        raw_extraction_dict = None
        if _is_tabular(filename):
            _, rows = _read_tabular(filename, content)
            plan = document_plan or DocumentPlan()
            column_mapping = plan
            if plan.entity_type == "cost_items":
                logger.info("extract_cost_rows_from_csv: file=%s link_hints=%s",
                            filename, plan.link_hints)
                result = _extract_cost_rows_from_csv(rows, plan)
            else:
                logger.info("extract_rows_parallel: file=%s entity_type=%s link_hints=%s",
                            filename, plan.entity_type, plan.link_hints)
                result = await _extract_rows_parallel(filename, rows, plan, space=space, job_id=job_id)
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
                source_job_id=job_id, created_at=now,
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
                cost = CostItem(
                    cost_id=cost_id, space_id=space_id, event_id=ev_id,
                    source_job_id=job_id, created_at=_now_iso(), **rec.payload,
                )
            except Exception:
                logger.exception("cost build failed: payload=%s", rec.payload)
                continue
            db.document(f"cost_items/{cost_id}").set(cost.model_dump(), merge=True)

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

    # 1) バッチ横断 AI 理解（Step1: 全ファイルのヘッダー+サンプルをフルモデルへ一括投入）
    document_plans = await understand_batch(files, hint, space=space)

    # 2) 観測着地 + 解釈（ファイル並列）
    interps = list(await asyncio.gather(
        *[_interpret_file(fn, content, hint, db, space=space,
                          document_plan=document_plans.get(fn))
          for fn, content in files]
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

    # 3) 確定（conform）→ 4) 結合（bind）→ 5) 導出（derive）
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
