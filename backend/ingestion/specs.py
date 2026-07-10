"""
specs — IngestionSpec レジストリ（データセット追加 = ontology.py のモデル + ここに1エントリ）

各データセットの取り込み仕様を宣言する。ここから自動導出されるもの（INGESTION_MAPPING §6）:
  1. プロンプトのオントロジー定義（prompts.render_ontology_definition）
  2. 抽出スキーマ（observation）とモデルの整合チェック（_check_registry。import 時に実行）
  3. 汎用ビルダー（engine の処理6種別）
  4. 確定/結合ステージのループ（role と links から機械的に決まる）

手書きのまま残るのは prompt_context の一段落・observation モデル宣言・特殊な normalizer・
新しい「振る舞い」（skip_check）のみ。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ingestion.normalize import (
    Normalizer,
    int_with_unit,
    iso_date,
    money_jpy,
    percent_rate,
)
from ontology import (
    Account,
    Content,
    ContentType,
    CostCategory,
    CostItem,
    Event,
    EventAttendance,
    Person,
    Product,
    ProductInterest,
)

# ── スペックの型 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinkSpec:
    """他マスタへのリンク（FK）の宣言。"""

    target: str  # リンク先のレジストリキー（"events" | "accounts" | "products"）
    required: bool = False  # 必須リンクが未解決 → 観測は pending へ（黙って捨てない）
    default_from_batch: bool = False  # 確認済みバッチ既定イベントで埋めてよいか
    obs_field: str = ""  # observation 上のフィールド名（"" なら "{kind}_link_name"）
    many: bool = False  # list 型リンク（セル値は _split_names で分割）


@dataclass(frozen=True)
class AppealSpec:
    """appeal_summary / appeal_vector 生成の宣言（semantic_search.build_appeal に渡す）。"""

    kind: str  # summary_kind（"event" | "product" | "content"）
    payload_fields: tuple[str, ...]  # payload に載せるモデルフィールド


@dataclass(frozen=True)
class IngestionSpec:
    """1データセットの取り込み仕様。"""

    kind: str  # レジストリキー
    role: str  # "master" | "fact" | "patch"
    model: type[BaseModel]  # ontology.py のモデル（真実源）
    collection: str  # 保存先コレクション
    id_field: str
    id_prefix: str
    natural_key: tuple[str, ...] = ()  # 名寄せキー（master のみ）
    fuzzy: bool = False  # EntityResolver の包含一致フォールバック可否
    links: dict[str, LinkSpec] = field(default_factory=dict)
    # 抽出用の薄いスキーマ（全フィールド任意 + リンク名 + skip_reason）。
    # None = ファイルの直接ターゲットにならない（persons / product_interests）。
    observation: type[BaseModel] | None = None
    # observation が正当に含む別モデルのフィールド（attendance の Person フィールド等）
    co_models: tuple[type[BaseModel], ...] = ()
    prompt_context: str = ""  # 業務的意味の一段落（プロンプトに埋める。唯一の手書き散文）
    normalizers: dict[str, Normalizer] = field(default_factory=dict)
    enum_defaults: dict[str, Any] = field(default_factory=dict)  # 未知値の既定（モデル既定に優先）
    appeal: AppealSpec | None = None
    patch_target: str | None = None  # role="patch" のときの畳み込み先レジストリキー
    # 解釈済みデータの最小要件チェック（None なら natural_key 先頭の非空を確認）
    skip_check: Callable[[dict], str | None] | None = None

    def link_obs_field(self, kind: str) -> str:
        ls = self.links[kind]
        return ls.obs_field or f"{kind}_link_name"


# ── observation モデル（抽出スキーマ。全フィールド任意 + skip_reason）─────────────


class AttendanceObservation(BaseModel):
    """参加者リスト1行 = 1接客の観測。person / account / attendance / interest の素。"""

    name: str | None = None
    name_last: str | None = None  # 姓（name への合成入力）
    name_first: str | None = None  # 名（name への合成入力）
    email: str | None = None
    company_name: str | None = None  # account リンク名
    department: str | None = None
    job_title: str | None = None
    owner_staff: str | None = None
    challenge_note: str | None = None
    memo: str | None = None
    action_type: str | None = None
    event_link_name: str | None = None
    product_link_names: list[str] = []
    skip_reason: str | None = None


class EventObservation(BaseModel):
    name: str | None = None
    event_type: str | None = None
    status: str | None = None
    venue: str | None = None
    event_date: str | None = None
    event_date_end: str | None = None
    booth_number: str | None = None
    total_budget: float | None = None
    target_contact_count: int | None = None
    description: str | None = None
    skip_reason: str | None = None


class AccountObservation(BaseModel):
    account_name: str | None = None
    industry_type: str | None = None
    company_size: str | None = None
    skip_reason: str | None = None


class ProductObservation(BaseModel):
    product_name: str | None = None
    product_category: str | None = None
    skip_reason: str | None = None


class ContentObservation(BaseModel):
    content_name: str | None = None
    content_type: str | None = None
    url: str | None = None
    description: str | None = None
    event_link_name: str | None = None
    skip_reason: str | None = None


class CostObservation(BaseModel):
    category: str | None = None
    description: str | None = None
    amount_jpy: float | None = None
    vendor_name: str | None = None
    invoice_date: str | None = None
    event_link_name: str | None = None
    skip_reason: str | None = None


class EventKPIObservation(BaseModel):
    """イベント KPI（events へのパッチ）。"""

    total_visitors_to_booth: int | None = None
    total_contacts_collected: int | None = None
    appointments_booked: int | None = None
    demo_sessions_held: int | None = None
    follow_email_open_rate: float | None = None
    follow_email_reply_rate: float | None = None
    pipeline_value_jpy: float | None = None
    closed_deals_3m: int | None = None
    closed_revenue_3m_jpy: float | None = None
    event_link_name: str | None = None
    skip_reason: str | None = None


class SurveyObservation(BaseModel):
    """アンケート集計（events へのパッチ）。"""

    nps_score: float | None = None
    total_survey_responses: int | None = None
    event_link_name: str | None = None
    skip_reason: str | None = None


# ── skip_check（最小要件。「黙って捨てる」のではなく理由付き skipped にする）─────


def _skip_attendance(data: dict) -> str | None:
    if not (data.get("name") or "").strip():
        return "氏名が空のためスキップ"
    return None


def _skip_cost(data: dict) -> str | None:
    if float(data.get("amount_jpy") or 0) <= 0:
        return "amount_jpy が 0 以下のためスキップ"
    return None


# ── レジストリ ─────────────────────────────────────────────────────────────────

REGISTRY: dict[str, IngestionSpec] = {
    # ---- マスタ ----
    "persons": IngestionSpec(
        kind="persons",
        role="master",
        model=Person,
        collection="persons",
        id_field="person_id",
        id_prefix="person_",
        natural_key=("email", "name", "company_name"),  # email 優先、無ければ 氏名×会社
        fuzzy=False,
        observation=None,  # ファイルの直接ターゲットにならない（接客観測から導出される）
        prompt_context=(
            "人物マスタ（ハウスリスト）。参加者リストの観測から名寄せで導出される"
            "派生ディメンションであり、ファイルの直接の取り込み先種別ではない。"
            "感度・ステータス等の業務的判定は行わない。"
        ),
    ),
    "accounts": IngestionSpec(
        kind="accounts",
        role="master",
        model=Account,
        collection="accounts",
        id_field="account_id",
        id_prefix="account_",
        natural_key=("account_name",),
        fuzzy=True,
        observation=AccountObservation,
        prompt_context="企業マスタ。参加者の所属企業や企業リストから確定される。",
    ),
    "events": IngestionSpec(
        kind="events",
        role="master",
        model=Event,
        collection="events",
        id_field="event_id",
        id_prefix="event_",
        natural_key=("name",),
        fuzzy=True,
        observation=EventObservation,
        prompt_context=(
            "イベントマスタ（展示会・セミナー・プライベートイベント）。"
            "概要ドキュメントや年間計画書から確定される。KPI・NPS はイベントへ畳み込む。"
        ),
        normalizers={
            "total_budget": money_jpy,
            "target_contact_count": int_with_unit,
            "event_date": iso_date,
            "event_date_end": iso_date,
        },
        appeal=AppealSpec("event", ("name", "event_type", "venue", "description")),
    ),
    "products": IngestionSpec(
        kind="products",
        role="master",
        model=Product,
        collection="products",
        id_field="product_id",
        id_prefix="product_",
        natural_key=("product_name",),
        fuzzy=True,
        observation=ProductObservation,
        prompt_context="自社製品マスタ。製品一覧ファイルや接客記録の関心製品から確定される。",
        appeal=AppealSpec("product", ("product_name", "product_category")),
    ),
    "contents": IngestionSpec(
        kind="contents",
        role="master",
        model=Content,
        collection="contents",
        id_field="content_id",
        id_prefix="content_",
        natural_key=("content_name",),
        fuzzy=True,
        links={"event": LinkSpec("events")},
        observation=ContentObservation,
        prompt_context=(
            "マーケ素材マスタ（資料・ホワイトペーパー / 導入事例 / ウェビナーアーカイブ / "
            "募集中セミナー・イベント）。素材一覧ファイルから確定される。"
        ),
        enum_defaults={"content_type": ContentType.WHITE_PAPER},
        appeal=AppealSpec("content", ("content_name", "content_type", "description")),
    ),
    # ---- ファクト ----
    "event_attendances": IngestionSpec(
        kind="event_attendances",
        role="fact",
        model=EventAttendance,
        collection="event_attendances",
        id_field="attendance_id",
        id_prefix="att_",
        links={
            "event": LinkSpec(
                "events", required=True, default_from_batch=True, obs_field="event_link_name"
            ),
            "account": LinkSpec("accounts", obs_field="company_name"),
            "product": LinkSpec("products", obs_field="product_link_names", many=True),
        },
        observation=AttendanceObservation,
        co_models=(Person,),  # 観測は Person のフィールド（氏名・所属等）を正当に含む
        prompt_context=(
            "接客ファクト（参加者リストの1行 = 1接客）。人物・所属企業・接客担当・課題感・"
            "メモ・関心製品を含む。challenge_note には担当者主観の興味度"
            "（「感度A」「関心高め」等）もテキストのまま含める（分類しない）。"
        ),
        skip_check=_skip_attendance,
    ),
    "product_interests": IngestionSpec(
        kind="product_interests",
        role="fact",
        model=ProductInterest,
        collection="product_interests",
        id_field="interest_id",
        id_prefix="int_",
        observation=None,  # 接客観測の product_link_names から結合ステージで導出される
        prompt_context="製品関心ファクト（persons × products）。接客観測の関心製品から導出される。",
    ),
    "cost_items": IngestionSpec(
        kind="cost_items",
        role="fact",
        model=CostItem,
        collection="cost_items",
        id_field="cost_id",
        id_prefix="cost_",
        links={
            "event": LinkSpec(
                "events", required=True, default_from_batch=True, obs_field="event_link_name"
            )
        },
        observation=CostObservation,
        prompt_context="費用ファクト（イベント費用の明細）。展示会・セミナー共通。必ず特定のイベントにリンクされる。",
        normalizers={"amount_jpy": money_jpy, "invoice_date": iso_date},
        enum_defaults={"category": CostCategory.OTHER},
        skip_check=_skip_cost,
    ),
    # ---- パッチ（既存マスタへの追記）----
    "event_kpi": IngestionSpec(
        kind="event_kpi",
        role="patch",
        model=Event,
        collection="events",
        id_field="event_id",
        id_prefix="event_",
        links={
            "event": LinkSpec(
                "events", required=True, default_from_batch=True, obs_field="event_link_name"
            )
        },
        observation=EventKPIObservation,
        prompt_context=(
            "イベント KPI（ブース来訪数・獲得名刺数・アポ数・パイプライン金額・成約等）。"
            "イベント実績レポートから抽出し、当該イベントへ畳み込む。"
        ),
        normalizers={
            "total_visitors_to_booth": int_with_unit,
            "total_contacts_collected": int_with_unit,
            "appointments_booked": int_with_unit,
            "demo_sessions_held": int_with_unit,
            "follow_email_open_rate": percent_rate,
            "follow_email_reply_rate": percent_rate,
            "pipeline_value_jpy": money_jpy,
            "closed_deals_3m": int_with_unit,
            "closed_revenue_3m_jpy": money_jpy,
        },
        patch_target="events",
    ),
    "survey_summary": IngestionSpec(
        kind="survey_summary",
        role="patch",
        model=Event,
        collection="events",
        id_field="event_id",
        id_prefix="event_",
        links={
            "event": LinkSpec(
                "events", required=True, default_from_batch=True, obs_field="event_link_name"
            )
        },
        observation=SurveyObservation,
        prompt_context="アンケート集計（NPS・回答数）。アンケート結果ドキュメントから当該イベントへ畳み込む。",
        normalizers={"total_survey_responses": int_with_unit},
        patch_target="events",
    ),
}

# 姓名合成の入力フィールド（name への合成。整合チェックの許容対象）
NAME_PART_FIELDS = ("name_last", "name_first")


def file_target_kinds() -> list[str]:
    """FilePlan.targets の entity_type に指定できる種別（observation を持つもの）。"""
    return [k for k, s in REGISTRY.items() if s.observation is not None]


# ── 整合チェック（ドリフトをテスト失敗 / import 失敗に変える）──────────────────────


def _model_field_names(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields.keys())


def _check_registry(registry: dict[str, IngestionSpec] | None = None) -> None:
    """observation ⊆ model ∪ co_models ∪ リンク obs_field ∪ {skip_reason, 姓名合成} を検証する。"""
    registry = REGISTRY if registry is None else registry
    errors: list[str] = []
    for kind, spec in registry.items():
        if spec.kind != kind:
            errors.append(f"{kind}: kind の不一致 ({spec.kind})")
        if spec.role not in ("master", "fact", "patch"):
            errors.append(f"{kind}: 不正な role '{spec.role}'")
        if spec.role == "master" and not spec.natural_key:
            errors.append(f"{kind}: master には natural_key が必要")
        if spec.role == "patch" and spec.patch_target not in registry:
            errors.append(f"{kind}: patch_target '{spec.patch_target}' がレジストリに無い")
        for link_kind, ls in spec.links.items():
            if ls.target not in registry:
                errors.append(f"{kind}: リンク '{link_kind}' の target '{ls.target}' が未登録")
            elif registry[ls.target].role != "master":
                errors.append(f"{kind}: リンク '{link_kind}' の target がマスタでない")
        if spec.observation is None:
            continue
        allowed = _model_field_names(spec.model)
        for co in spec.co_models:
            allowed |= _model_field_names(co)
        allowed |= {spec.link_obs_field(k) for k in spec.links}
        allowed |= {"skip_reason", *NAME_PART_FIELDS}
        extra = _model_field_names(spec.observation) - allowed
        if extra:
            errors.append(f"{kind}: observation の未対応フィールド {sorted(extra)}")
        unknown_norm = set(spec.normalizers) - _model_field_names(spec.observation)
        if unknown_norm:
            errors.append(f"{kind}: normalizer の対象が observation に無い {sorted(unknown_norm)}")
    if errors:
        raise RuntimeError("IngestionSpec レジストリの整合エラー:\n" + "\n".join(errors))


_check_registry()
