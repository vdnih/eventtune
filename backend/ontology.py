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


# ── 取り込みプラン（BatchPlan = 承認と実行の契約。ADR-015）───────────────────


class TargetPlan(BaseModel):
    """1ファイル内の1エンティティ種別ぶんの変換仕様。"""

    entity_type: str  # ingestion.specs.REGISTRY のキー
    column_map: dict[str, str] = {}  # {元列: observation フィールド}
    column_modes: dict[str, str] = {}  # {元列: "direct" | "ai_parse"}
    link_columns: dict[str, str] = {}  # {リンク種別: 元列}（行ごとにリンク先が異なる列）


class FilePlan(BaseModel):
    """1ファイルの変換仕様。1ファイルが複数種別（targets）を含み得る。"""

    filename: str
    business_context: str = ""  # 業務的な理解（例: "2025秋展示会の接客記録"）
    targets: list[TargetPlan] = []
    unmapped_notes: str = ""  # 対応づけられなかった列・不明点（確認画面に出す）
    extraction_caveat: str = (
        ""  # フォーマット起因の定型注意（PDF/PPTX）。P1 が上書きする。AI は設定しない
    )


class DefaultEventPlan(BaseModel):
    """バッチ既定イベントの提案。Confirm でユーザーが承認/変更/「なし」を選ぶ。"""

    name: str
    is_existing: bool = False  # 既存イベント照合の結果（P1 が計算。AI は設定しない）
    evidence: str = ""  # AI の提案根拠（Confirm に表示）


class BatchPlan(BaseModel):
    """バッチ全体の変換仕様。/plan が返し、承認済みのものが /batches でそのまま実行される。"""

    default_event: DefaultEventPlan | None = None
    files: list[FilePlan] = []


class SourceRecord(BaseModel):
    """取り込みの着地ゾーン（source_records）。観測ブロック1件=1ドキュメント。

    取り込み「プロセス」のデータであり OSI データセットではない（YAML には足さない）。
    保留（pending）の置き場・再処理の入力・監査の突合先を兼ねる（ADR-015）。
    """

    record_id: str
    space_id: str = ""
    batch_id: str
    filename: str
    row_no: int = 0
    raw: dict = {}  # {元列: 値}（文書は {"text": 全文}）
    status: str = "pending"  # "bound" | "pending" | "skipped"
    reason: str = ""
    refs: dict[str, list[str]] = {}  # {"persons": [...], "event_attendances": [...]}
    created_at: str = ""


# ── 統合ジョブ（旧 DataLineage + integration_batches を統合）────────────────


class TransformDecision(BaseModel):
    field: str
    value: str
    reason: str  # Optional 不可
    source_signals: dict[str, str] = {}


class SkippedRecord(BaseModel):
    entity_type: str
    reason: str
    detail: str = ""


class IntegrationJob(BaseModel):
    """データ統合バッチのジョブ記録（旧 DataLineage + integration_batches を統合）。

    ADR-015: 承認済み BatchPlan（承認と実行の契約）・ステージのハートビート・
    保留/スキップ集計・バッチ報告（Markdown）をバッチ単位で保持する。
    """

    job_id: str
    space_id: str
    filenames: list[str] = []
    # ユーザーの自然言語ヒント（Understand への曖昧解消の補助入力）。
    hint: str = ""
    status: str = "queued"  # "queued" | "processing" | "done" | "error"
    stage: str = ""  # "read" | "interpret" | "conform" | "bind" | "derive" | "report"
    heartbeat_at: str = ""  # ステージ毎に更新。停滞検知（stale sweep）に使う
    plan: BatchPlan | None = None  # 承認済みの変換仕様（実行されたものそのもの）
    created_entities: dict[str, int] = {}
    pending_count: int = 0  # リンク未解決の保留観測（source_records.status=pending）
    skipped_count: int = 0
    report_markdown: str = ""  # バッチ報告（P1 集計を AI が整形。チャットに表示）
    # このジョブで解決・生成したリンク先マスタの要約 [{kind, name, id, resolved_by}]。
    resolved_links: list[dict] = []
    skipped_records: list[SkippedRecord] = []
    error: str | None = None
    created_at: str = ""
