"""
OntologyMapper — DataIntegrationPipeline の変換フェーズ（決定論 Python）

DataIntegrationAgent (AI) が出力した生データを、決定論的ロジックで最終的な
オントロジーエンティティ（OSI 星座型: 5マスタ＋ファクト）に分解する。AI は使わない。

ADR-008 / docs/INGESTION_MAPPING.md に従う:
- ファイルはレコードの容れ物。行を persons/accounts/events/products/contents＋ファクトへ分解する。
- イベントは経路キーではなくリンク（FK）。リンクは「列 → ファイル既定（ヒント）→ 名寄せ」で解決する。
- マスタの名寄せは name からの安定 ID（find-or-create を ID 規約で実現、DB 往復不要・冪等）。
- appeal_summary / appeal_vector は非同期 AI 生成のため本モジュールでは付けない
  （DataIntegrationAgent が semantic_search で後付けする）。

Auditable AI（原則4）に従い、各変換判定の根拠を EntityTransformation として返す。
"""

import hashlib
import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from ontology import (
    Account,
    ColumnMappingResult,
    ContactStage,
    Content,
    ContentType,
    CostCategory,
    CostItem,
    DocumentExtractionResult,
    EngagementLevel,
    EntityTransformation,
    Event,
    EventAttendance,
    EventStatus,
    EventType,
    Person,
    Product,
    ProductInterest,
    SkippedRecord,
    TransformDecision,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, key: str) -> str:
    """同じ key に対して常に同じ ID を返す（マスタ重複防止・冪等な find-or-create）。"""
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{prefix}{digest}"


def stable_event_id(space_id: str, name: str) -> str:
    return _stable_id("event_", f"{space_id}:{name.strip().lower()}")


def stable_account_id(space_id: str, name: str) -> str:
    return _stable_id("account_", f"{space_id}:{name.strip().lower()}")


def stable_product_id(space_id: str, name: str) -> str:
    return _stable_id("product_", f"{space_id}:{name.strip().lower()}")


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _split_names(text: str) -> list[str]:
    """区切り文字で複数の名称に分割する（製品名セルの「A、B」等）。"""
    if not text:
        return []
    parts = re.split(r"[、,/／;；]", text)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        # 長すぎるセルはメモ等の可能性が高く、製品名として扱わない
        if s and len(s) <= 60 and s not in out:
            out.append(s)
    return out


# 各ビルダーの戻り値: (生成エンティティ | None, 加工根拠 | None, スキップ記録 | None)
BuildResult = tuple[Any | None, EntityTransformation | None, SkippedRecord | None]


@dataclass
class MapResult:
    """map_* の戻り値。

    referenced_masters: 行から名前で参照されたマスタ [(kind, id, name)]。
    DataIntegrationAgent が identity のみのスタブ doc を merge 書き込みし、
    リンク先マスタの存在を保証する（後から概要ファイル等で詳細が merge される）。
    """
    entities: list[Any] = field(default_factory=list)
    transformations: list[EntityTransformation] = field(default_factory=list)
    skipped: list[SkippedRecord] = field(default_factory=list)
    referenced_masters: list[tuple[str, str, str]] = field(default_factory=list)


class OntologyMapper:
    """AI 抽出結果 → 最終オントロジーモデル への決定論変換を担う。"""

    # ── パスA: 表形式データ ──────────────────────────────────────────────────

    def map_rows(
        self,
        column_mapping: ColumnMappingResult,
        rows: list[dict],
        space_id: str = "",
        job_id: str | None = None,
    ) -> MapResult:
        """CSV/Excel の行データを ColumnMappingResult に従いエンティティへ分解する。"""
        result = MapResult()
        entity_type = column_mapping.entity_type
        link_columns = column_mapping.link_columns or {}
        default_links = column_mapping.default_links or {}

        for row in rows:
            mapped = self._apply_column_map(column_mapping.column_map, row)
            if entity_type in ("persons", "contacts"):
                self._decompose_person(
                    mapped, link_columns, default_links, space_id, job_id, result,
                )
            elif entity_type == "events":
                self._merge(result, self._build_event(mapped, space_id=space_id))
            elif entity_type == "products":
                self._merge(result, self._build_product(mapped, space_id=space_id))
            elif entity_type == "contents":
                self._merge(result, self._build_content(mapped, space_id=space_id))
            elif entity_type == "cost_items":
                self._build_cost_row(mapped, link_columns, default_links, space_id, result)
        return result

    def _apply_column_map(self, column_map: dict[str, str], row: dict) -> dict:
        out: dict = {}
        for csv_col, ontology_field in column_map.items():
            val = row.get(csv_col, "")
            if val is None:
                val = ""
            out[ontology_field] = str(val).strip()
        out["__raw"] = row
        return out

    @staticmethod
    def _merge(result: MapResult, build: BuildResult) -> Any | None:
        entity, transform, skip = build
        if entity is not None:
            result.entities.append(entity)
        if transform is not None:
            result.transformations.append(transform)
        if skip is not None:
            result.skipped.append(skip)
        return entity

    def _resolve_link_name(
        self, kind: str, mapped: dict, link_columns: dict, default_links: dict
    ) -> str:
        """リンク先マスタ名を解決する: 行の列 → ファイル既定（ヒント由来）の順。"""
        col = link_columns.get(kind)
        if col:
            val = str(mapped.get("__raw", {}).get(col, "")).strip()
            if val:
                return val
        return str(default_links.get(kind, "")).strip()

    def _decompose_person(
        self,
        mapped: dict,
        link_columns: dict,
        default_links: dict,
        space_id: str,
        job_id: str | None,
        result: MapResult,
    ) -> None:
        """1行を Person + Account + [EventAttendance] + [ProductInterest] へ分解する。"""
        name_last = mapped.get("name_last", "")
        name_first = mapped.get("name_first", "")
        name = mapped.get("name", f"{name_last}{name_first}").strip()
        if not name:
            result.skipped.append(SkippedRecord(
                entity_type="Person",
                reason="name 空のためスキップ",
                detail=f"name_last={name_last!r} name_first={name_first!r}",
            ))
            return

        decisions: list[TransformDecision] = []

        engagement, eng_reason, eng_signals = self._classify_engagement(mapped)
        decisions.append(TransformDecision(
            field="engagement_level", value=engagement.value,
            reason=eng_reason, source_signals=eng_signals,
        ))

        # notes 集約
        notes_sources = {
            "__memo": mapped.get("__memo", ""),
            "__needs": mapped.get("__needs", ""),
            "__caution": mapped.get("__caution", ""),
        }
        merged_fields = [k for k, v in notes_sources.items() if v]
        notes = " / ".join(notes_sources[k] for k in merged_fields)
        if merged_fields:
            decisions.append(TransformDecision(
                field="notes", value=notes,
                reason="次のフィールドを集約: " + ", ".join(merged_fields),
                source_signals={k: notes_sources[k] for k in merged_fields},
            ))

        now = _now_iso()
        company_name = (
            mapped.get("company_name", "").strip()
            or self._resolve_link_name("account", mapped, link_columns, default_links)
        )

        # Account（企業マスター, 安定 ID で同名集約）
        account_id: str | None = None
        if company_name:
            account_id = stable_account_id(space_id, company_name)
            result.entities.append(Account(
                account_id=account_id, space_id=space_id,
                account_name=company_name, created_at=now,
            ))

        person = Person(
            person_id=_new_id("person_"),
            space_id=space_id,
            account_id=account_id,
            name=name,
            email=mapped.get("email") or None,
            department=mapped.get("department", ""),
            job_title=mapped.get("job_title", ""),
            stage=ContactStage.LEAD,
            engagement_level=engagement,
            extracted_challenge=mapped.get("extracted_challenge", ""),
            notes=notes,
            source_job_id=job_id,
            created_at=now,
        )
        result.entities.append(person)
        result.transformations.append(EntityTransformation(
            entity_type="Person", entity_id=person.person_id,
            source_label=f"{name}（{company_name}）" if company_name else name,
            decisions=decisions,
        ))

        # EventAttendance（参加ファクト）— イベントリンクを解決
        event_name = self._resolve_link_name("event", mapped, link_columns, default_links)
        if event_name:
            event_id = stable_event_id(space_id, event_name)
            result.referenced_masters.append(("event", event_id, event_name))
            result.entities.append(EventAttendance(
                attendance_id=_new_id("att_"), space_id=space_id,
                person_id=person.person_id, event_id=event_id,
                source_job_id=job_id, created_at=now,
            ))

        # ProductInterest（製品関心ファクト）— 製品名を products マスタへ名寄せ
        product_text = " ".join(filter(None, [
            self._resolve_link_name("product", mapped, link_columns, default_links),
            mapped.get("__product_signal", ""),
        ]))
        product_names = _split_names(product_text)
        for pname in product_names:
            product_id = stable_product_id(space_id, pname)
            result.referenced_masters.append(("product", product_id, pname))
            result.entities.append(ProductInterest(
                interest_id=_new_id("int_"), space_id=space_id,
                person_id=person.person_id, product_id=product_id,
                source_job_id=job_id, created_at=now,
            ))
        if product_names:
            result.transformations.append(EntityTransformation(
                entity_type="ProductInterest", entity_id=person.person_id,
                source_label=name,
                decisions=[TransformDecision(
                    field="interested_products", value=", ".join(product_names),
                    reason="製品名を products マスタへ名寄せ（安定ID, データ駆動）",
                    source_signals={"product_text": product_text},
                )],
            ))

    def _build_cost_row(
        self, mapped: dict, link_columns: dict, default_links: dict,
        space_id: str, result: MapResult,
    ) -> None:
        event_name = self._resolve_link_name("event", mapped, link_columns, default_links)
        if not event_name:
            result.skipped.append(SkippedRecord(
                entity_type="CostItem",
                reason="イベントリンク未解決のためスキップ",
                detail=f"description={mapped.get('description','')!r}",
            ))
            return
        event_id = stable_event_id(space_id, event_name)
        result.referenced_masters.append(("event", event_id, event_name))
        self._merge(result, self._build_cost_item(mapped, event_id=event_id))

    # ── パスB: 非構造化ドキュメント ─────────────────────────────────────────

    def map_extraction(
        self,
        extraction: DocumentExtractionResult,
        space_id: str = "",
        job_id: str | None = None,
    ) -> MapResult:
        """DocumentExtractor の出力を最終エンティティへ変換する。"""
        result = MapResult()
        resolved_event_id: str | None = None

        for event_data in extraction.events:
            event = self._merge(result, self._build_event(event_data, space_id=space_id))
            if event is not None and resolved_event_id is None:
                resolved_event_id = event.event_id

        if extraction.event_kpi and resolved_event_id:
            self._merge(result, self._build_event_kpi_patch(extraction.event_kpi, resolved_event_id))
        if extraction.survey_response and resolved_event_id:
            self._merge(result, self._build_survey_patch(extraction.survey_response, resolved_event_id))
        if extraction.cost_items and resolved_event_id:
            for raw_cost in extraction.cost_items:
                self._merge(result, self._build_cost_item(raw_cost, event_id=resolved_event_id))
        if extraction.content_assets:
            for raw_asset in extraction.content_assets:
                self._merge(result, self._build_content(raw_asset, space_id=space_id))

        return result

    # ── ビルダーメソッド ─────────────────────────────────────────────────────

    def _build_event(self, raw: dict, space_id: str = "") -> BuildResult:
        now = _now_iso()
        name = (raw.get("name") or "").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Event", reason="name 空のためスキップ", detail="",
            )
        event_type_map = {
            "展示会": EventType.TRADE_SHOW,
            "セミナー": EventType.SEMINAR,
            "プライベートイベント": EventType.PRIVATE_EVENT,
        }
        status_map = {
            "計画中": EventStatus.PLANNED,
            "開催中": EventStatus.ACTIVE,
            "終了": EventStatus.COMPLETED,
        }
        raw_type = raw.get("event_type", "")
        raw_status = raw.get("status", "")
        event_type = event_type_map.get(raw_type, EventType.TRADE_SHOW)
        status = status_map.get(raw_status, EventStatus.COMPLETED)
        budget = _to_float(raw.get("total_budget"))

        # 安定 ID で名寄せ（参加者ファイルからの event リンクと一致させる）
        event = Event(
            event_id=stable_event_id(space_id, name),
            space_id=space_id,
            name=name,
            event_type=event_type,
            status=status,
            venue=raw.get("venue") or "",
            event_date=raw.get("event_date") or "",
            event_date_end=raw.get("event_date_end") or raw.get("event_date") or "",
            booth_number=raw.get("booth_number") or None,
            total_budget=budget,
            target_contact_count=_to_int(raw.get("target_contact_count")),
            description=raw.get("description") or "",
            created_at=now,
            updated_at=now,
        )
        transform = EntityTransformation(
            entity_type="Event", entity_id=event.event_id,
            source_label=event.name,
            decisions=[
                TransformDecision(
                    field="event_type", value=event_type.value,
                    reason=(f"'{raw_type}' を enum にマッピング" if raw_type in event_type_map
                            else f"未知の値 '{raw_type}' → 既定(展示会)"),
                    source_signals={"event_type": raw_type},
                ),
                TransformDecision(
                    field="status", value=status.value,
                    reason=(f"'{raw_status}' を enum にマッピング" if raw_status in status_map
                            else f"未知の値 '{raw_status}' → 既定(終了)"),
                    source_signals={"status": raw_status},
                ),
            ],
        )
        return event, transform, None

    def _build_account(self, raw: dict, space_id: str = "") -> BuildResult:
        name = (raw.get("company_name") or raw.get("account_name") or "").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Account", reason="account_name 空のためスキップ", detail="",
            )
        account = Account(
            account_id=stable_account_id(space_id, name),
            space_id=space_id,
            account_name=name,
            industry_type=raw.get("industry_type") or "",
            company_size=raw.get("company_size") or "",
            created_at=_now_iso(),
        )
        transform = EntityTransformation(
            entity_type="Account", entity_id=account.account_id,
            source_label=account.account_name, decisions=[],
        )
        return account, transform, None

    def _build_product(self, raw: dict, space_id: str = "") -> BuildResult:
        name = (raw.get("product_name") or raw.get("name") or "").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Product", reason="product_name 空のためスキップ", detail="",
            )
        product = Product(
            product_id=stable_product_id(space_id, name),
            space_id=space_id,
            product_name=name,
            product_category=raw.get("product_category") or "",
            created_at=_now_iso(),
        )
        transform = EntityTransformation(
            entity_type="Product", entity_id=product.product_id,
            source_label=product.product_name, decisions=[],
        )
        return product, transform, None

    def _build_event_kpi_patch(self, raw: dict, event_id: str) -> BuildResult:
        """KPI を Event フィールドへ畳み込むパッチ（name/created_at 空 = パッチ印）。"""
        event = Event(
            event_id=event_id, name="", created_at="", updated_at=_now_iso(),
            total_visitors_to_booth=_to_int(raw.get("total_visitors_to_booth")) or None,
            total_contacts_collected=_to_int(raw.get("total_contacts_collected")) or None,
            appointments_booked=_to_int(raw.get("appointments_booked")) or None,
            demo_sessions_held=_to_int(raw.get("demo_sessions_held")) or None,
            follow_email_open_rate=_to_float(raw.get("follow_email_open_rate")) or None,
            follow_email_reply_rate=_to_float(raw.get("follow_email_reply_rate")) or None,
            pipeline_value_jpy=_to_float(raw.get("pipeline_value_jpy")) or None,
            closed_deals_3m=_to_int(raw.get("closed_deals_3m")) or None,
            closed_revenue_3m_jpy=_to_float(raw.get("closed_revenue_3m_jpy")) or None,
        )
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_id,
            source_label=f"KPI patch（event={event_id}）",
            decisions=[TransformDecision(
                field="pipeline_value_jpy", value=str(event.pipeline_value_jpy),
                reason="数値化して Event に畳み込み",
                source_signals={"pipeline_value_jpy": str(raw.get("pipeline_value_jpy", ""))},
            )],
        )
        return event, transform, None

    def _build_survey_patch(self, raw: dict, event_id: str) -> BuildResult:
        event = Event(
            event_id=event_id, name="", created_at="", updated_at=_now_iso(),
            nps_score=_to_float(raw.get("nps_score")) or None,
            total_survey_responses=_to_int(raw.get("total_responses")) or None,
        )
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_id,
            source_label=f"Survey patch（event={event_id}）",
            decisions=[TransformDecision(
                field="nps_score", value=str(event.nps_score),
                reason="NPS スコアを Event に畳み込み",
                source_signals={"nps_score": str(raw.get("nps_score", ""))},
            )],
        )
        return event, transform, None

    def _build_cost_item(self, raw: dict, event_id: str) -> BuildResult:
        category_map = {v.value: v for v in CostCategory}
        category_str = raw.get("category", "その他")
        category = category_map.get(category_str, CostCategory.OTHER)

        amount_raw = raw.get("amount_jpy", raw.get("amount", 0))
        try:
            amount = float(str(amount_raw).replace(",", "").replace("円", ""))
        except (ValueError, TypeError):
            amount = 0.0

        description = raw.get("description") or ""
        if amount <= 0:
            return None, None, SkippedRecord(
                entity_type="CostItem", reason="amount<=0 のためスキップ",
                detail=f"description={description!r} amount_raw={amount_raw!r}",
            )

        cost = CostItem(
            cost_id=_new_id("cost_"), event_id=event_id, category=category,
            description=description, amount_jpy=amount,
            vendor_name=raw.get("vendor_name") or None,
            invoice_date=raw.get("invoice_date") or None,
        )
        transform = EntityTransformation(
            entity_type="CostItem", entity_id=cost.cost_id,
            source_label=description or cost.cost_id,
            decisions=[
                TransformDecision(
                    field="amount_jpy", value=str(amount),
                    reason=f"'{amount_raw}' からカンマ・'円' を除去して数値化",
                    source_signals={"amount_raw": str(amount_raw)},
                ),
                TransformDecision(
                    field="category", value=category.value,
                    reason=(f"'{category_str}' を enum にマッピング" if category_str in category_map
                            else f"未知の値 '{category_str}' → 既定(その他)"),
                    source_signals={"category": str(category_str)},
                ),
            ],
        )
        return cost, transform, None

    def _build_content(self, raw: dict, space_id: str = "") -> BuildResult:
        type_map = {v.value: v for v in ContentType}
        raw_type = raw.get("content_type", "")
        content_type = type_map.get(raw_type, ContentType.WHITE_PAPER)
        name = (raw.get("name") or raw.get("content_name") or "").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Content", reason="name 空のためスキップ",
                detail=f"content_type={raw_type!r}",
            )
        content = Content(
            content_id=raw.get("asset_id") or raw.get("content_id") or _new_id("content_"),
            space_id=space_id,
            content_name=name,
            content_type=content_type,
            url=raw.get("url") or "",
            description=raw.get("description") or "",
            linked_event_id=raw.get("linked_event_id") or None,
        )
        transform = EntityTransformation(
            entity_type="Content", entity_id=content.content_id,
            source_label=content.content_name,
            decisions=[TransformDecision(
                field="content_type", value=content_type.value,
                reason=(f"'{raw_type}' を enum にマッピング" if raw_type in type_map
                        else f"未知の値 '{raw_type}' → 既定(資料・ホワイトペーパー)"),
                source_signals={"content_type": str(raw_type)},
            )],
        )
        return content, transform, None

    # ── 決定論的分類ロジック ─────────────────────────────────────────────────

    def _classify_engagement(
        self, mapped: dict
    ) -> tuple[EngagementLevel, str, dict[str, str]]:
        """EngagementLevel を決定論的ルールで分類する（AI 不使用）。

        汎用シグナル方式: 判定ランク（A/B/C）・温度感・メモから決める。
        """
        judgment = mapped.get("__engagement_signal", "").strip().upper()
        temperature = mapped.get("__temperature_signal", "").strip()
        memo = mapped.get("__memo", "").strip()
        signals = {
            "__engagement_signal": judgment,
            "__temperature_signal": temperature,
            "__memo": memo,
        }

        if judgment == "A":
            return EngagementLevel.APPOINTMENT_BOOKED, "判定シグナル=A", signals
        if any(kw in memo for kw in ("アポ", "アポイント", "アポ取得", "アポ設定")):
            return EngagementLevel.APPOINTMENT_BOOKED, "memo に「アポ」を含む", signals

        if judgment == "B":
            return EngagementLevel.HIGH_INTENT, "判定シグナル=B", signals
        if temperature in ("ホット", "ウォーム", "高", "中", "hot", "warm"):
            return EngagementLevel.HIGH_INTENT, f"温度感シグナル={temperature}", signals

        return EngagementLevel.NURTURING, "該当ルールなし → 既定(通常リード)", signals
