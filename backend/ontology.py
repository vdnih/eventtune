from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# ── Contact ──────────────────────────────────────────────────────────────────

class EngagementLevel(str, Enum):
    APPOINTMENT_BOOKED = "アポ獲得済み"
    HIGH_INTENT        = "アポなし・感度高"
    NURTURING          = "通常リード"


class ContactStage(str, Enum):
    LEAD     = "LEAD"
    MQL      = "MQL"
    SQL      = "SQL"
    CUSTOMER = "CUSTOMER"
    EXCLUDED = "EXCLUDED"


class Product(str, Enum):
    PRODUCT_A = "プロダクトA"
    PRODUCT_B = "プロダクトB"


class Contact(BaseModel):
    contact_id: str
    name: str
    company_name: str
    department: str
    job_title: str
    email: Optional[str] = None
    stage: ContactStage = ContactStage.LEAD
    # EngagementLevel は stage=LEAD のときのみ有効
    engagement_level: Optional[EngagementLevel] = None
    interested_products: List[Product] = []
    extracted_challenge: str = ""
    # 名刺メモ・担当者所感など、構造化できない文脈情報を格納
    notes: str = ""
    source_event_id: Optional[str] = None


# ── Event ─────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    TRADE_SHOW    = "展示会"
    SEMINAR       = "セミナー"
    PRIVATE_EVENT = "プライベートイベント"


class EventStatus(str, Enum):
    PLANNED   = "計画中"
    ACTIVE    = "開催中"
    COMPLETED = "終了"


class Event(BaseModel):
    event_id: str
    name: str
    event_type: EventType
    status: EventStatus
    venue: str
    event_date: str           # ISO date YYYY-MM-DD
    event_date_end: str
    booth_number: Optional[str] = None
    total_budget: float       # JPY
    target_contact_count: int
    # 概要・目的・担当者所感など、構造化できない文脈情報を格納
    description: str = ""
    created_at: str
    updated_at: str


# ── EventKPI ──────────────────────────────────────────────────────────────────

class EngagementCounts(BaseModel):
    appointment_booked: int
    high_intent: int
    nurturing: int


class EventKPI(BaseModel):
    kpi_id: str
    event_id: str
    total_visitors_to_booth: int
    total_contacts_collected: int
    contacts_by_engagement: EngagementCounts
    appointments_booked: int
    demo_sessions_held: int
    follow_email_open_rate: float    # 0.0–1.0
    follow_email_reply_rate: float
    pipeline_value_jpy: float
    closed_deals_3m: int
    closed_revenue_3m_jpy: float
    created_at: str


# ── SurveyResponse ────────────────────────────────────────────────────────────

class SatisfactionCategory(str, Enum):
    BOOTH_DESIGN    = "ブースデザイン"
    PRODUCT_DEMO    = "製品デモ"
    STAFF_RESPONSE  = "スタッフ対応"
    CONTENT_QUALITY = "コンテンツ品質"
    OVERALL         = "総合満足度"


class SatisfactionScore(BaseModel):
    category: SatisfactionCategory
    avg_score: float             # 1.0–5.0
    response_count: int


class SurveyResponse(BaseModel):
    survey_id: str
    event_id: str
    total_responses: int
    nps_score: float             # -100 to 100
    nps_promoters: int
    nps_passives: int
    nps_detractors: int
    satisfaction_scores: List[SatisfactionScore]
    verbatim_positives: List[str]
    verbatim_negatives: List[str]
    verbatim_suggestions: List[str]
    created_at: str


# ── CostItem ──────────────────────────────────────────────────────────────────

class CostCategory(str, Enum):
    BOOTH_RENTAL = "ブース出展料"
    BOOTH_DESIGN = "ブース装飾・設営"
    EQUIPMENT    = "機材・備品"
    STAFFING     = "人件費・派遣"
    TRAVEL       = "交通・宿泊"
    PRINTING     = "印刷・販促物"
    CATERING     = "飲食・接待"
    OTHER        = "その他"


class CostItem(BaseModel):
    cost_id: str
    event_id: str
    category: CostCategory
    description: str
    amount_jpy: float
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None   # ISO date


class CostSummary(BaseModel):
    total_jpy: float
    by_category: dict[str, float]        # CostCategory.value → amount


# ── ContentAsset ──────────────────────────────────────────────────────────────

class ContentType(str, Enum):
    SEMINAR_UPCOMING = "未来のセミナー（募集中）"
    EVENT_UPCOMING   = "未来のイベント（募集中）"
    WHITE_PAPER      = "資料・ホワイトペーパー"
    CASE_STUDY       = "導入事例"


class ContentAsset(BaseModel):
    asset_id: str
    content_type: ContentType
    name: str
    description: str
    url: str
    # 開催予定セミナー/イベントと Event エンティティを紐付ける（任意）
    linked_event_id: Optional[str] = None


# ── ComposedEmail ─────────────────────────────────────────────────────────────

class EmailBlock(BaseModel):
    block_type: str
    # Auditable AI: AIがこのブロックを選んだ理由。Optional 不可
    reason_for_inclusion: str
    associated_asset_ids: List[str] = []
    block_text: str


class ComposedEmail(BaseModel):
    email_id: str
    contact_id: str
    event_id: Optional[str] = None
    run_id: str
    subject: str
    blocks: List[EmailBlock]
    created_at: str


# ── DataLineage ───────────────────────────────────────────────────────────────

class ColumnMappingResult(BaseModel):
    """SchemaMapper（パスA）の出力: CSVカラム → オントロジーフィールドのマッピング"""
    entity_type: str                      # "contacts" / "cost_items" など
    column_map: dict[str, str]            # "会社名" → "company_name"
                                          # "__" プレフィックスは Python ロジックで変換
    unmapped_columns: List[str] = []
    event_routing_column: Optional[str] = None  # 行ごとに異なるイベントへルーティングする列名


class DocumentExtractionResult(BaseModel):
    """DocumentExtractor（パスB）の出力: 非構造化ドキュメントから抽出したエンティティ群"""
    detected_entity_types: List[str]      # ["event", "event_kpi", "cost_items", ...]
    events: List[dict] = []               # 0件 or 複数件のイベント（1ドキュメント複数イベント対応）
    event_kpi: Optional[dict] = None
    cost_items: Optional[List[dict]] = None
    survey_response: Optional[dict] = None
    content_assets: Optional[List[dict]] = None


# ── 加工処理レポート（ステージ2: OntologyMapper の決定論的変換の根拠） ──────────
# Auditable AI（原則4）: AI一次処理の後に走る Python 加工処理も「なぜそう判定したか」を
# 記録し、後追い可能にする。reason は Optional 不可。

class TransformDecision(BaseModel):
    """ステージ2の1判定。1フィールドの変換結果とその根拠を保持する。"""
    field: str                            # "engagement_level" / "interested_products" / "amount_jpy" ...
    value: str                            # 変換後の値（文字列化）
    # Auditable AI: なぜそう判定したか。Optional 不可
    reason: str
    source_signals: dict[str, str] = {}   # 判定に使った生シグナル


class EntityTransformation(BaseModel):
    """ステージ2で1エンティティを生成する際に行った加工判定の集合。"""
    entity_type: str                      # "Contact" / "CostItem" / "Event" ...
    entity_id: str
    source_label: str                     # 人が識別できる名前（contact名 / cost説明 / event名）
    decisions: List[TransformDecision] = []


class SkippedRecord(BaseModel):
    """ステージ2でスキップされたレコード（エンティティ化されなかった入力）。"""
    entity_type: str
    reason: str                           # "amount<=0 のためスキップ" / "name 空のためスキップ"
    detail: str = ""


class TransformationSummary(BaseModel):
    """バッチ単位の加工処理サマリ。"""
    entity_counts: dict[str, int] = {}          # {"Contact": 42, "CostItem": 5}
    engagement_breakdown: dict[str, int] = {}   # {"アポ獲得済み": 3, ...}
    product_breakdown: dict[str, int] = {}      # {"プロダクトA": 12, ...}
    skipped_count: int = 0


class DataLineage(BaseModel):
    """データの来歴記録。変換経緯を保存する（UIは将来実装）"""
    lineage_id: str
    source_filename: str
    source_type: str                      # "tabular" | "unstructured"
    batch_id: str
    # パスA の場合
    column_mapping: Optional[ColumnMappingResult] = None
    # パスB の場合
    raw_extraction: Optional[dict] = None
    created_entity_ids: dict[str, List[str]] = {}   # {"events": [...], "contacts": [...]}
    # ステージ2（OntologyMapper）の加工処理レポート
    transformations: List[EntityTransformation] = []
    skipped_records: List[SkippedRecord] = []
    transformation_summary: Optional[TransformationSummary] = None
    created_at: str
