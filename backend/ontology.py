from enum import Enum

from pydantic import BaseModel

# ── Space（テナント）/ メンバー / 利用状況 ─────────────────────────────────────


class Role(str, Enum):
    OWNER = "owner"
    MEMBER = "member"


class Plan(str, Enum):
    FREE = "free"
    PRO = "pro"
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
    llm: dict[str, dict[str, int]] = {}
    compute: dict[str, dict[str, int]] = {}


# ── 共通 Enum ────────────────────────────────────────────────────────────────


class ContactStage(str, Enum):
    LEAD = "LEAD"
    MQL = "MQL"
    SQL = "SQL"
    CUSTOMER = "CUSTOMER"
    EXCLUDED = "EXCLUDED"


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
    account_id: str | None = None
    name: str
    email: str | None = None
    department: str = ""
    job_title: str = ""
    stage: ContactStage = ContactStage.LEAD
    # 接客事実（課題感・メモ）は EventAttendance へ移譲（ADR-011）。
    # 本人の関心・文脈は appeal_summary に全 attendance を集約してロールアップ生成する。
    appeal_summary: str = ""
    appeal_vector: list[float] = []
    source_job_id: str | None = None
    created_at: str = ""


class Product(BaseModel):
    """製品マスター。"""

    product_id: str
    space_id: str
    product_name: str
    product_category: str = ""
    appeal_summary: str = ""
    appeal_vector: list[float] = []
    created_at: str = ""


# ── ファクトデータ ─────────────────────────────────────────────────────────────


class EventAttendance(BaseModel):
    """イベント参加ファクト（Person × Event の多対多）。

    接客(encounter)時の事実をここに保持する（ADR-011）: 接客担当・課題感・所感メモ。
    Person.appeal_summary はこれら全 attendance を集約して導出される。
    """

    attendance_id: str
    space_id: str
    person_id: str
    event_id: str
    action_type: str = "参加"  # "申し込み" | "参加" | "アンケート高評価"
    owner_staff: str = ""  # 接客担当者
    challenge_note: str = ""  # その接客で把握した課題感
    memo: str = ""  # 所感・要望・注意などの自由メモ
    source_job_id: str | None = None
    created_at: str = ""


class ProductInterest(BaseModel):
    """製品関心ファクト（Person × Product の多対多）。"""

    interest_id: str
    space_id: str
    person_id: str
    product_id: str
    interest_status: str = "興味あり"  # "興味あり" | "デモ実施済み" | "キャンセル"
    source_job_id: str | None = None
    created_at: str = ""


class CostCategory(str, Enum):
    VENUE = "会場費・出展費"
    BOOTH_DESIGN = "ブース装飾・設営"
    MARKETING = "集客"
    SPEAKER = "登壇者"
    STAFFING = "人件費・派遣"
    TRAVEL = "交通・宿泊"
    PRINTING = "印刷・販促物・ノベルティ"
    OPERATIONS = "運営"
    CATERING = "飲食・接待"
    OTHER = "その他"


class CostItem(BaseModel):
    """費用ファクト（Event に紐づく費用明細）。展示会・セミナー共通。"""

    cost_id: str
    space_id: str
    event_id: str
    category: CostCategory
    description: str
    amount_jpy: float
    vendor_name: str | None = None
    invoice_date: str | None = None
    source_job_id: str | None = None
    created_at: str = ""


class CostSummary(BaseModel):
    total_jpy: float
    by_category: dict[str, float]


# ── Event ─────────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    TRADE_SHOW = "展示会"
    SEMINAR = "セミナー"
    PRIVATE_EVENT = "プライベートイベント"


class EventStatus(str, Enum):
    PLANNED = "計画中"
    ACTIVE = "開催中"
    COMPLETED = "終了"


class Event(BaseModel):
    """イベント。KPI・NPS 集計値を畳み込み（旧 EventKPI / SurveyResponse を統合）。

    取り込みでは参加者ファイル等から「イベント名」だけで参照（リンク）されることがある。
    その場合 identity フィールドのみのスタブとして書き込まれ、後から概要ファイル等で
    詳細が merge される。スタブが read 側で valid となるよう必須スカラーに既定値を持つ。
    """

    event_id: str
    space_id: str = ""
    name: str
    event_type: EventType = EventType.TRADE_SHOW
    status: EventStatus = EventStatus.COMPLETED
    venue: str = ""
    event_date: str = ""
    event_date_end: str = ""
    booth_number: str | None = None
    total_budget: float = 0.0
    target_contact_count: int = 0
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    # KPI（旧 EventKPI から畳み込み）
    total_visitors_to_booth: int | None = None
    total_contacts_collected: int | None = None
    appointments_booked: int | None = None
    demo_sessions_held: int | None = None
    follow_email_open_rate: float | None = None
    follow_email_reply_rate: float | None = None
    pipeline_value_jpy: float | None = None
    closed_deals_3m: int | None = None
    closed_revenue_3m_jpy: float | None = None
    # Survey 集計値（旧 SurveyResponse から畳み込み）
    nps_score: float | None = None
    total_survey_responses: int | None = None
    # Semantic layer
    appeal_summary: str = ""
    appeal_vector: list[float] = []


# ── Content（旧 ContentAsset を改名・拡張）───────────────────────────────────


class ContentType(str, Enum):
    SEMINAR_UPCOMING = "未来のセミナー（募集中）"
    EVENT_UPCOMING = "未来のイベント（募集中）"
    WHITE_PAPER = "資料・ホワイトペーパー"
    CASE_STUDY = "導入事例"
    WEBINAR_ARCHIVE = "ウェビナーアーカイブ"


class Content(BaseModel):
    """コンテンツアセット（旧 ContentAsset を改名）。"""

    content_id: str
    space_id: str = ""
    content_name: str
    content_type: ContentType
    url: str
    description: str = ""
    linked_event_id: str | None = None
    appeal_summary: str = ""
    appeal_vector: list[float] = []


# ── Segment ───────────────────────────────────────────────────────────────────


class SegmentAxis(BaseModel):
    name: str
    values: list[str]


class Segment(BaseModel):
    segment_id: str
    name: str
    purpose: str
    axes: list[SegmentAxis]
    buckets: list[str]
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
    associated_asset_ids: list[str] = []


class DeliverablePattern(BaseModel):
    """バケット単位のコンテンツパターン（成果物のひな型）。

    pattern_id は "{bucket}__{format}" 規約。本文中の個人差分はプレースホルダで表現し、
    組み立て（run_assembly）で決定論的に置換する。
    """

    pattern_id: str
    segment_id: str = ""
    bucket: str
    format: str = "EMAIL"  # "EMAIL" | "TALK_SCRIPT" | "PROPOSAL"
    subject: str = ""
    blocks: list[DeliverableBlock] = []
    created_at: str = ""


class Deliverable(BaseModel):
    """生成成果物（メール / トークスクリプト / 提案書 など）。"""

    deliverable_id: str
    space_id: str
    run_id: str
    person_id: str
    event_id: str | None = None
    snapshot_id: str | None = None
    pattern_id: str | None = None
    format: str = "EMAIL"  # "EMAIL" | "TALK_SCRIPT" | "PROPOSAL"
    bucket: str = ""
    subject: str | None = None
    blocks: list[DeliverableBlock] = []
    created_at: str = ""


class MarketingRun(BaseModel):
    """個別カスタマイズの組み立てジョブ（marketing_runs/{run_id}）。"""

    run_id: str
    space_id: str = ""
    status: str = "running"  # "running" | "done" | "error"
    segment_id: str = ""
    snapshot_id: str = ""
    purpose: str = ""
    total: int = 0
    done: int = 0
    deliverable_count: int = 0
    created_at: str = ""


# ── 統合ジョブ（旧 DataLineage + integration_batches を統合）────────────────


class ColumnMappingResult(BaseModel):
    entity_type: str
    column_map: dict[str, str]
    unmapped_columns: list[str] = []
    # 行ごとに異なるリンク先（マスタ）を識別する列。{kind: カラム名}。
    # kind は "event" | "account" | "product"。旧 event_routing_column を一般化したもの。
    link_columns: dict[str, str] = {}
    # ファイル全体に適用するリンク先（行に列が無いとき）。{kind: マスタ名}。
    # ヒントやファイル文脈から AI が推定する（例 {"event": "2025秋展示会"}）。
    default_links: dict[str, str] = {}


class DocumentPlan(BaseModel):
    """AI Extract Step1 の出力。1ファイルの業務的理解結果。integration_jobs に保存する。"""

    business_context: str = ""  # "2025秋展示会の参加者接客記録"
    entity_type: str = ""  # "persons" | "events" | "products" | ...
    source_file_role: str = ""  # "participant_list" | "event_master" | "costs" | ...
    link_hints: dict[str, str] = {}  # {"event": "2025秋展示会"}
    column_map: dict[str, str] = {}  # {"氏名": "name", "社名": "account_name", ...}
    unmapped_notes: str = ""


class DocumentExtractionResult(BaseModel):
    detected_entity_types: list[str]
    events: list[dict] = []
    event_kpi: dict | None = None
    cost_items: list[dict] | None = None
    survey_response: dict | None = None
    content_assets: list[dict] | None = None


class TransformDecision(BaseModel):
    field: str
    value: str
    reason: str  # Optional 不可
    source_signals: dict[str, str] = {}


class EntityTransformation(BaseModel):
    entity_type: str
    entity_id: str
    source_label: str
    decisions: list[TransformDecision] = []


class SkippedRecord(BaseModel):
    entity_type: str
    reason: str
    detail: str = ""


class TransformationSummary(BaseModel):
    entity_counts: dict[str, int] = {}
    product_breakdown: dict[str, int] = {}
    skipped_count: int = 0


class IntegrationJob(BaseModel):
    """データ統合ジョブ（旧 DataLineage + integration_batches を統合）。"""

    job_id: str
    space_id: str
    filenames: list[str] = []
    # ユーザーの自然言語ヒント（曖昧なリンク解決・スコープ指定の補正に使う）。
    hint: str = ""
    status: str = "queued"  # "queued" | "running" | "done" | "error"
    created_entities: dict[str, int] = {}
    # このジョブで解決・生成したリンク先マスタの要約 [{kind, name, id}]。
    resolved_links: list[dict] = []
    column_mapping: DocumentPlan | None = None
    raw_extraction: dict | None = None
    transformations: list[EntityTransformation] = []
    skipped_records: list[SkippedRecord] = []
    transformation_summary: TransformationSummary | None = None
    partial: bool = False
    error: str | None = None
    created_at: str = ""
