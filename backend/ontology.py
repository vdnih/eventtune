from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel


# ── Space（テナント）/ メンバー / 利用状況 ─────────────────────────────────────

class Role(str, Enum):
    OWNER  = "owner"
    MEMBER = "member"


class Plan(str, Enum):
    FREE    = "free"
    PRO     = "pro"
    PREMIUM = "premium"


class Space(BaseModel):
    space_id: str
    name: str
    plan: Plan = Plan.FREE
    owner_uid: str
    description: str = ""
    created_at: str
    updated_at: str


class SpaceMember(BaseModel):
    user_id: str
    email: str
    role: Role = Role.MEMBER
    space_id: str
    space_name: str
    joined_at: str


class UsagePeriod(BaseModel):
    """月次のリソース消費の生実績。
    - llm:     モデル種別ごとの入出力トークン   {model: {"input_tokens": int, "output_tokens": int}}
    - compute: リソース種別ごとの実行時間(ms)   {resource_type: {"ms": int}}
    """
    period: str
    llm: Dict[str, Dict[str, int]] = {}
    compute: Dict[str, Dict[str, int]] = {}


# ── 共通 Enum ────────────────────────────────────────────────────────────────

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


class ProductCode(str, Enum):
    """製品コード Enum（名寄せ・キーワードマッチに使用）。
    旧名 Product — ADR-008 で Product は BaseModel マスターに変更されたため改名。
    """
    PRODUCT_A = "プロダクトA"
    PRODUCT_B = "プロダクトB"


# ── マスターデータ ─────────────────────────────────────────────────────────────

class Account(BaseModel):
    """企業マスター。Person を束ねる単位。"""
    account_id: str
    space_id: str
    account_name: str
    industry_type: str = ""
    company_size: str = ""
    created_at: str = ""


class Person(BaseModel):
    """個人（旧 Contact を分解・再定義）。
    企業情報は Account へ、参加履歴は EventAttendance へ、製品興味は ProductInterest へ分離。
    """
    person_id: str
    space_id: str
    account_id: Optional[str] = None
    name: str
    email: Optional[str] = None
    department: str = ""
    job_title: str = ""
    stage: ContactStage = ContactStage.LEAD
    engagement_level: Optional[EngagementLevel] = None
    extracted_challenge: str = ""
    notes: str = ""
    appeal_summary: str = ""
    appeal_vector: List[float] = []
    source_job_id: Optional[str] = None
    source_file_id: Optional[str] = None
    created_at: str = ""


class Product(BaseModel):
    """製品マスター。"""
    product_id: str
    space_id: str
    product_name: str
    product_category: str = ""
    appeal_summary: str = ""
    appeal_vector: List[float] = []
    created_at: str = ""


# ── ファクトデータ ─────────────────────────────────────────────────────────────

class EventAttendance(BaseModel):
    """イベント参加ファクト（Person × Event の多対多）。"""
    attendance_id: str
    space_id: str
    person_id: str
    event_id: str
    action_type: str = "参加"  # "申し込み" | "参加"
    source_job_id: Optional[str] = None
    created_at: str = ""


class ProductInterest(BaseModel):
    """製品関心ファクト（Person × Product の多対多）。"""
    interest_id: str
    space_id: str
    person_id: str
    product_id: str
    interest_status: str = "興味あり"  # "興味あり" | "デモ実施済み" | "キャンセル"
    source_job_id: Optional[str] = None
    created_at: str = ""


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
    """イベント。KPI・NPS 集計値を畳み込み（旧 EventKPI / SurveyResponse を統合）。"""
    event_id: str
    space_id: str = ""
    name: str
    event_type: EventType
    status: EventStatus
    venue: str
    event_date: str
    event_date_end: str
    booth_number: Optional[str] = None
    total_budget: float = 0.0
    target_contact_count: int = 0
    description: str = ""
    created_at: str
    updated_at: str
    # KPI（旧 EventKPI から畳み込み）
    total_visitors_to_booth: Optional[int] = None
    total_contacts_collected: Optional[int] = None
    appointments_booked: Optional[int] = None
    demo_sessions_held: Optional[int] = None
    follow_email_open_rate: Optional[float] = None
    follow_email_reply_rate: Optional[float] = None
    pipeline_value_jpy: Optional[float] = None
    closed_deals_3m: Optional[int] = None
    closed_revenue_3m_jpy: Optional[float] = None
    # Survey 集計値（旧 SurveyResponse から畳み込み）
    nps_score: Optional[float] = None
    total_survey_responses: Optional[int] = None
    # Semantic layer
    appeal_summary: str = ""
    appeal_vector: List[float] = []


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
    invoice_date: Optional[str] = None


class CostSummary(BaseModel):
    total_jpy: float
    by_category: dict[str, float]


# ── Content（旧 ContentAsset を改名・拡張）───────────────────────────────────

class ContentType(str, Enum):
    SEMINAR_UPCOMING = "未来のセミナー（募集中）"
    EVENT_UPCOMING   = "未来のイベント（募集中）"
    WHITE_PAPER      = "資料・ホワイトペーパー"
    CASE_STUDY       = "導入事例"


class Content(BaseModel):
    """コンテンツアセット（旧 ContentAsset を改名）。"""
    content_id: str
    space_id: str = ""
    content_name: str
    content_type: ContentType
    url: str
    description: str = ""
    linked_event_id: Optional[str] = None
    appeal_summary: str = ""
    appeal_vector: List[float] = []


# ── Segment ───────────────────────────────────────────────────────────────────

class SegmentAxis(BaseModel):
    name: str
    values: List[str]


class Segment(BaseModel):
    segment_id: str
    name: str
    purpose: str
    axes: List[SegmentAxis]
    buckets: List[str]
    criteria: str
    created_at: str


class SegmentSnapshot(BaseModel):
    """セグメント割り当てのスナップショット（バージョン管理）。"""
    snapshot_id: str
    segment_id: str
    space_id: str
    version: str
    by_bucket: dict[str, int] = {}
    created_at: str = ""


class SegmentAssignment(BaseModel):
    """1人のセグメントにおける所属バケットと根拠。"""
    person_id: str
    segment_id: str
    snapshot_id: str
    space_id: str
    bucket: str
    reason: str  # Auditable AI（Optional 不可）
    source_signals: dict[str, str] = {}


# ── Deliverable（旧 ComposedEmail / EmailBlock を汎用化）────────────────────

class DeliverableBlock(BaseModel):
    block_type: str
    block_text: str = ""
    reason_for_inclusion: str  # Optional 不可
    associated_asset_ids: List[str] = []


class Deliverable(BaseModel):
    """生成成果物（メール / トークスクリプト / 提案書 など）。"""
    deliverable_id: str
    space_id: str
    run_id: str
    person_id: str
    event_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    pattern_id: Optional[str] = None
    format: str = "EMAIL"  # "EMAIL" | "TALK_SCRIPT" | "PROPOSAL"
    bucket: str = ""
    subject: Optional[str] = None
    blocks: List[DeliverableBlock] = []
    created_at: str = ""


# ── 統合ジョブ（旧 DataLineage + integration_batches を統合）────────────────

class ColumnMappingResult(BaseModel):
    entity_type: str
    column_map: dict[str, str]
    unmapped_columns: List[str] = []
    event_routing_column: Optional[str] = None


class DocumentExtractionResult(BaseModel):
    detected_entity_types: List[str]
    events: List[dict] = []
    event_kpi: Optional[dict] = None
    cost_items: Optional[List[dict]] = None
    survey_response: Optional[dict] = None
    content_assets: Optional[List[dict]] = None


class TransformDecision(BaseModel):
    field: str
    value: str
    reason: str  # Optional 不可
    source_signals: dict[str, str] = {}


class EntityTransformation(BaseModel):
    entity_type: str
    entity_id: str
    source_label: str
    decisions: List[TransformDecision] = []


class SkippedRecord(BaseModel):
    entity_type: str
    reason: str
    detail: str = ""


class TransformationSummary(BaseModel):
    entity_counts: dict[str, int] = {}
    engagement_breakdown: dict[str, int] = {}
    product_breakdown: dict[str, int] = {}
    skipped_count: int = 0


class IntegrationJob(BaseModel):
    """データ統合ジョブ（旧 DataLineage + integration_batches を統合）。"""
    job_id: str
    space_id: str
    filenames: List[str] = []
    file_event_map: dict = {}
    status: str = "queued"  # "queued" | "running" | "done" | "error"
    created_entities: dict[str, int] = {}
    event_ids: List[str] = []
    column_mapping: Optional[ColumnMappingResult] = None
    raw_extraction: Optional[dict] = None
    transformations: List[EntityTransformation] = []
    skipped_records: List[SkippedRecord] = []
    transformation_summary: Optional[TransformationSummary] = None
    partial: bool = False
    error: Optional[str] = None
    created_at: str = ""


# ── 廃止モデル（後方互換のため残存、新規コードでは使わないこと）────────────────

class EngagementCounts(BaseModel):
    """廃止: Event.appointments_booked 等に畳み込み済み。"""
    appointment_booked: int
    high_intent: int
    nurturing: int


class EventKPI(BaseModel):
    """廃止: Event モデルに KPI フィールドを畳み込み済み。"""
    kpi_id: str
    event_id: str
    total_visitors_to_booth: int
    total_contacts_collected: int
    contacts_by_engagement: EngagementCounts
    appointments_booked: int
    demo_sessions_held: int
    follow_email_open_rate: float
    follow_email_reply_rate: float
    pipeline_value_jpy: float
    closed_deals_3m: int
    closed_revenue_3m_jpy: float
    created_at: str


class SatisfactionCategory(str, Enum):
    """廃止: SurveyResponse ごと廃止予定。"""
    BOOTH_DESIGN    = "ブースデザイン"
    PRODUCT_DEMO    = "製品デモ"
    STAFF_RESPONSE  = "スタッフ対応"
    CONTENT_QUALITY = "コンテンツ品質"
    OVERALL         = "総合満足度"


class SatisfactionScore(BaseModel):
    """廃止: SurveyResponse ごと廃止予定。"""
    category: SatisfactionCategory
    avg_score: float
    response_count: int


class SurveyResponse(BaseModel):
    """廃止: 集計値は Event モデルに畳み込み済み。"""
    survey_id: str
    event_id: str
    total_responses: int
    nps_score: float
    nps_promoters: int
    nps_passives: int
    nps_detractors: int
    satisfaction_scores: List[SatisfactionScore]
    verbatim_positives: List[str]
    verbatim_negatives: List[str]
    verbatim_suggestions: List[str]
    created_at: str


class Contact(BaseModel):
    """廃止: Person + Account + EventAttendance + ProductInterest に分解済み。"""
    contact_id: str
    name: str
    company_name: str
    department: str
    job_title: str
    email: Optional[str] = None
    stage: ContactStage = ContactStage.LEAD
    engagement_level: Optional[EngagementLevel] = None
    interested_products: List[ProductCode] = []
    extracted_challenge: str = ""
    notes: str = ""
    source_event_id: Optional[str] = None


class EmailBlock(BaseModel):
    """廃止: DeliverableBlock に改名済み。"""
    block_type: str
    reason_for_inclusion: str
    associated_asset_ids: List[str] = []
    block_text: str


class ComposedEmail(BaseModel):
    """廃止: Deliverable に汎用化済み。"""
    email_id: str
    contact_id: str
    event_id: Optional[str] = None
    run_id: str
    subject: str
    blocks: List[EmailBlock]
    created_at: str


class ContentAsset(BaseModel):
    """廃止: Content に改名・拡張済み。"""
    asset_id: str
    content_type: ContentType
    name: str
    description: str
    url: str
    linked_event_id: Optional[str] = None


class DataLineage(BaseModel):
    """廃止: IntegrationJob に統合済み。"""
    lineage_id: str
    source_filename: str
    source_type: str
    batch_id: str
    column_mapping: Optional[ColumnMappingResult] = None
    raw_extraction: Optional[dict] = None
    created_entity_ids: dict[str, List[str]] = {}
    transformations: List[EntityTransformation] = []
    skipped_records: List[SkippedRecord] = []
    transformation_summary: Optional[TransformationSummary] = None
    created_at: str
