"""
OntologyMapper — DataIntegrationPipeline の変換フェーズ

DataIntegrationAgent (AI) が出力した生データを、決定論的ロジックで
最終的なオントロジーエンティティに変換する。
EngagementLevel 判定・Product 名寄せなど、ビジネスルールはここに集約する。
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ontology import (
    Account,
    ColumnMappingResult,
    Contact,
    ContactStage,
    Content,
    ContentAsset,
    ContentType,
    CostCategory,
    CostItem,
    DocumentExtractionResult,
    EngagementCounts,
    EngagementLevel,
    EntityTransformation,
    Event,
    EventAttendance,
    EventKPI,
    EventStatus,
    EventType,
    Person,
    ProductCode,
    ProductInterest,
    SatisfactionCategory,
    SatisfactionScore,
    SkippedRecord,
    SurveyResponse,
    TransformDecision,
)


# ── Product 名寄せキーワード ─────────────────────────────────────────────────

_PRODUCT_A_KEYWORDS = {
    "スキルマップ", "技能伝承", "資格管理", "資格・安全", "安全講習",
    "多能工", "haccp", "HACCP", "技能", "スキル", "プロダクトa", "プロダクトA",
}

_PRODUCT_B_KEYWORDS = {
    "要員配置", "シフト", "ローテーション", "プロダクトb", "プロダクトB",
    "人員配置", "シフト管理", "配置最適化",
}

# ProductCode.value → product_id のマッピング（スペース横断で固定値）
_PRODUCT_CODE_TO_ID = {
    ProductCode.PRODUCT_A.value: "product_a",
    ProductCode.PRODUCT_B.value: "product_b",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, key: str) -> str:
    """同じ key に対して常に同じ ID を返す（企業マスター重複防止）。"""
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{prefix}{digest}"


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


# 各ビルダーの戻り値: (生成エンティティ | None, 加工根拠 | None, スキップ記録 | None)
BuildResult = tuple[Any | None, EntityTransformation | None, SkippedRecord | None]
# map_* の戻り値: (エンティティ群, 加工根拠群, スキップ記録群)
MapResult = tuple[list[Any], list[EntityTransformation], list[SkippedRecord]]


class OntologyMapper:
    """AI 抽出結果 → 最終オントロジーモデル への変換を担う。AI は使わない。

    Auditable AI（原則4）に従い、各変換判定の根拠を EntityTransformation として
    返し、後追い可能なレポートを生成できるようにする。
    """

    # ── パスA: 表形式データ ──────────────────────────────────────────────────

    def map_rows(
        self,
        column_mapping: ColumnMappingResult,
        rows: list[dict],
        event_id: str | None = None,
        batch_id: str | None = None,
        space_id: str = "",
        job_id: str | None = None,
    ) -> MapResult:
        """CSV/Excel の行データを、ColumnMappingResult に従いエンティティに変換する。"""
        results: list[Any] = []
        transforms: list[EntityTransformation] = []
        skipped: list[SkippedRecord] = []
        for row in rows:
            mapped = self._apply_column_map(column_mapping.column_map, row)
            if column_mapping.entity_type in ("contacts", "persons"):
                entities, transform, skip = self._decompose_person(
                    mapped,
                    event_id=event_id,
                    space_id=space_id,
                    job_id=job_id,
                )
                if entities:
                    results.extend(entities)
                if transform is not None:
                    transforms.append(transform)
                if skip is not None:
                    skipped.append(skip)
            elif column_mapping.entity_type == "cost_items":
                entity, transform, skip = self._build_cost_item(mapped, event_id=event_id or "")
                if entity is not None:
                    results.append(entity)
                if transform is not None:
                    transforms.append(transform)
                if skip is not None:
                    skipped.append(skip)
        return results, transforms, skipped

    def _apply_column_map(self, column_map: dict[str, str], row: dict) -> dict:
        out: dict = {}
        for csv_col, ontology_field in column_map.items():
            val = row.get(csv_col, "")
            if val is None:
                val = ""
            out[ontology_field] = str(val).strip()
        out["__raw"] = row
        return out

    def _decompose_person(
        self,
        mapped: dict,
        event_id: str | None = None,
        space_id: str = "",
        job_id: str | None = None,
    ) -> tuple[list[Any] | None, EntityTransformation | None, SkippedRecord | None]:
        """1行データを Person + Account + EventAttendance + [ProductInterest] に分解する。

        Returns:
            ([entities], transform, skip) — entities は None でなければ複数エンティティを含む。
        """
        name_last = mapped.get("name_last", "")
        name_first = mapped.get("name_first", "")
        name = mapped.get("name", f"{name_last}{name_first}").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Person",
                reason="name 空のためスキップ",
                detail=f"name_last={name_last!r} name_first={name_first!r}",
            )

        decisions: list[TransformDecision] = []

        # EngagementLevel の決定論的分類
        engagement, eng_reason, eng_signals = self._classify_engagement(mapped)
        decisions.append(TransformDecision(
            field="engagement_level",
            value=engagement.value,
            reason=eng_reason,
            source_signals=eng_signals,
        ))

        # Product の名寄せ
        product_text = (
            mapped.get("__product_signal", "")
            + " "
            + mapped.get("extracted_challenge", "")
            + " "
            + mapped.get("notes", "")
        )
        product_codes, matched_keywords = self._match_products(product_text)
        decisions.append(TransformDecision(
            field="interested_products",
            value=", ".join(p.value for p in product_codes) or "（なし）",
            reason=(
                "キーワードマッチ: " + ", ".join(matched_keywords)
                if matched_keywords else "マッチするキーワードなし"
            ),
            source_signals={"__product_signal": mapped.get("__product_signal", "")},
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
                field="notes",
                value=notes,
                reason="次のフィールドを集約: " + ", ".join(merged_fields),
                source_signals={k: notes_sources[k] for k in merged_fields},
            ))

        now = _now_iso()
        company_name = mapped.get("company_name", "").strip()

        # Account（企業マスター）— 同名企業は同じ account_id に集約
        account: Account | None = None
        account_id: str | None = None
        if company_name:
            account_id = _stable_id("account_", f"{space_id}:{company_name.lower()}")
            account = Account(
                account_id=account_id,
                space_id=space_id,
                account_name=company_name,
                created_at=now,
            )

        # Person
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

        transform = EntityTransformation(
            entity_type="Person",
            entity_id=person.person_id,
            source_label=f"{name}（{company_name}）" if company_name else name,
            decisions=decisions,
        )

        entities: list[Any] = [person]
        if account is not None:
            entities.append(account)

        # EventAttendance（参加ファクト）
        if event_id:
            attendance = EventAttendance(
                attendance_id=_new_id("att_"),
                space_id=space_id,
                person_id=person.person_id,
                event_id=event_id,
                source_job_id=job_id,
                created_at=now,
            )
            entities.append(attendance)

        # ProductInterest（製品関心ファクト）
        for pc in product_codes:
            product_id = _PRODUCT_CODE_TO_ID.get(pc.value, pc.value)
            interest = ProductInterest(
                interest_id=_new_id("int_"),
                space_id=space_id,
                person_id=person.person_id,
                product_id=product_id,
                source_job_id=job_id,
                created_at=now,
            )
            entities.append(interest)

        return entities, transform, None

    # ── パスB: 非構造化ドキュメント ─────────────────────────────────────────

    def map_extraction(
        self,
        extraction: DocumentExtractionResult,
        event_id: str | None = None,
        batch_id: str | None = None,
        space_id: str = "",
        job_id: str | None = None,
        event_id_resolver: Callable[[str], str | None] | None = None,
    ) -> MapResult:
        """DocumentExtractor の出力を最終エンティティに変換する。"""
        results: list[Any] = []
        transforms: list[EntityTransformation] = []
        skipped: list[SkippedRecord] = []
        resolved_event_id = event_id

        def _collect(result: BuildResult) -> Any | None:
            entity, transform, skip = result
            if entity is not None:
                results.append(entity)
            if transform is not None:
                transforms.append(transform)
            if skip is not None:
                skipped.append(skip)
            return entity

        for event_data in extraction.events:
            effective_event_id = event_id
            matched_name: str | None = None
            if event_id is None and event_id_resolver is not None:
                name = (event_data.get("name") or "").strip()
                if name:
                    existing_id = event_id_resolver(name)
                    if existing_id:
                        effective_event_id = existing_id
                        matched_name = name

            result = self._build_event(event_data, event_id=effective_event_id, space_id=space_id)
            if matched_name is not None:
                _entity, transform, _skip = result
                if transform is not None:
                    transform.decisions.append(TransformDecision(
                        field="event_id",
                        value=effective_event_id or "",
                        reason=f"同名イベント '{matched_name}' が既存（{effective_event_id}）→ 新規採番せず統合",
                        source_signals={"name": matched_name},
                    ))
            event = _collect(result)
            if event is not None and resolved_event_id is None:
                resolved_event_id = event.event_id

        # KPI → Event に merge（同じ event_id の Event ドキュメントを上書き）
        if extraction.event_kpi and resolved_event_id:
            _collect(self._build_event_kpi_patch(extraction.event_kpi, event_id=resolved_event_id))

        # Survey → Event に merge
        if extraction.survey_response and resolved_event_id:
            _collect(self._build_survey_patch(extraction.survey_response, event_id=resolved_event_id))

        if extraction.cost_items and resolved_event_id:
            for raw_cost in extraction.cost_items:
                _collect(self._build_cost_item(raw_cost, event_id=resolved_event_id))

        if extraction.content_assets:
            for raw_asset in extraction.content_assets:
                _collect(self._build_content(raw_asset, space_id=space_id))

        return results, transforms, skipped

    # ── ビルダーメソッド ─────────────────────────────────────────────────────

    def _build_event(
        self, raw: dict, event_id: str | None = None, space_id: str = ""
    ) -> BuildResult:
        now = _now_iso()
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

        event = Event(
            event_id=event_id or raw.get("event_id") or _new_id("event_"),
            space_id=space_id,
            name=raw.get("name", ""),
            event_type=event_type,
            status=status,
            venue=raw.get("venue", ""),
            event_date=raw.get("event_date", ""),
            event_date_end=raw.get("event_date_end") or raw.get("event_date", ""),
            booth_number=raw.get("booth_number") or None,
            total_budget=budget,
            target_contact_count=_to_int(raw.get("target_contact_count")),
            description=raw.get("description", ""),
            created_at=now,
            updated_at=now,
        )
        decisions = [
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
            TransformDecision(
                field="total_budget", value=str(budget),
                reason="数値化（カンマ・通貨記号除去はAI側で実施済み）",
                source_signals={"total_budget": str(raw.get("total_budget", ""))},
            ),
        ]
        transform = EntityTransformation(
            entity_type="Event", entity_id=event.event_id,
            source_label=event.name or event.event_id, decisions=decisions,
        )
        return event, transform, None

    def _build_event_kpi_patch(self, raw: dict, event_id: str) -> BuildResult:
        """KPI データを Event フィールドとしてパッチする（旧 _build_event_kpi の代替）。

        返される Event は KPI フィールドのみ設定されており、Firestore への merge で
        既存 Event ドキュメントの KPI フィールドだけを更新する。
        """
        by_engagement = raw.get("contacts_by_engagement", {})
        # 既存イベントの必須フィールドはダミー値で埋め、merge=True で書き込む
        event = Event(
            event_id=event_id,
            name="",
            event_type=EventType.TRADE_SHOW,
            status=EventStatus.COMPLETED,
            venue="",
            event_date="",
            event_date_end="",
            created_at="",
            updated_at=_now_iso(),
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
        decisions = [
            TransformDecision(
                field="pipeline_value_jpy", value=str(event.pipeline_value_jpy),
                reason="数値化して Event に畳み込み",
                source_signals={"pipeline_value_jpy": str(raw.get("pipeline_value_jpy", ""))},
            ),
        ]
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_id,
            source_label=f"KPI patch（event={event_id}）", decisions=decisions,
        )
        return event, transform, None

    def _build_survey_patch(self, raw: dict, event_id: str) -> BuildResult:
        """Survey 集計値を Event フィールドとしてパッチする（旧 _build_survey_response の代替）。"""
        event = Event(
            event_id=event_id,
            name="",
            event_type=EventType.TRADE_SHOW,
            status=EventStatus.COMPLETED,
            venue="",
            event_date="",
            event_date_end="",
            created_at="",
            updated_at=_now_iso(),
            nps_score=_to_float(raw.get("nps_score")) or None,
            total_survey_responses=_to_int(raw.get("total_responses")) or None,
        )
        decisions = [TransformDecision(
            field="nps_score", value=str(event.nps_score),
            reason="NPS スコアを Event に畳み込み",
            source_signals={"nps_score": str(raw.get("nps_score", ""))},
        )]
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_id,
            source_label=f"Survey patch（event={event_id}）", decisions=decisions,
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

        description = raw.get("description", "")
        if amount <= 0:
            return None, None, SkippedRecord(
                entity_type="CostItem",
                reason="amount<=0 のためスキップ",
                detail=f"description={description!r} amount_raw={amount_raw!r}",
            )

        cost = CostItem(
            cost_id=_new_id("cost_"),
            event_id=event_id,
            category=category,
            description=description,
            amount_jpy=amount,
            vendor_name=raw.get("vendor_name") or None,
            invoice_date=raw.get("invoice_date") or None,
        )
        decisions = [
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
        ]
        transform = EntityTransformation(
            entity_type="CostItem", entity_id=cost.cost_id,
            source_label=description or cost.cost_id, decisions=decisions,
        )
        return cost, transform, None

    def _build_content(self, raw: dict, space_id: str = "") -> BuildResult:
        """コンテンツアセット（旧 _build_content_asset → Content に改名）。"""
        type_map = {v.value: v for v in ContentType}
        raw_type = raw.get("content_type", "")
        content_type = type_map.get(raw_type, ContentType.WHITE_PAPER)
        name = raw.get("name", "")
        if not name:
            return None, None, SkippedRecord(
                entity_type="Content",
                reason="name 空のためスキップ",
                detail=f"content_type={raw_type!r}",
            )
        content = Content(
            content_id=raw.get("asset_id") or raw.get("content_id") or _new_id("content_"),
            space_id=space_id,
            content_name=name,
            content_type=content_type,
            url=raw.get("url", ""),
            description=raw.get("description", ""),
            linked_event_id=raw.get("linked_event_id") or None,
        )
        decisions = [TransformDecision(
            field="content_type", value=content_type.value,
            reason=(f"'{raw_type}' を enum にマッピング" if raw_type in type_map
                    else f"未知の値 '{raw_type}' → 既定(資料・ホワイトペーパー)"),
            source_signals={"content_type": str(raw_type)},
        )]
        transform = EntityTransformation(
            entity_type="Content", entity_id=content.content_id,
            source_label=content.content_name, decisions=decisions,
        )
        return content, transform, None

    # ── 廃止済みメソッド（data_integration_agent.py の移行完了まで残存）──────

    def _build_event_kpi(self, raw: dict, event_id: str) -> BuildResult:
        """廃止: _build_event_kpi_patch を使うこと。後方互換のため残存。"""
        return self._build_event_kpi_patch(raw, event_id)

    def _build_survey_response(self, raw: dict, event_id: str) -> BuildResult:
        """廃止: _build_survey_patch を使うこと。後方互換のため残存。"""
        return self._build_survey_patch(raw, event_id)

    def _build_content_asset(self, raw: dict) -> BuildResult:
        """廃止: _build_content を使うこと。後方互換のため残存。"""
        return self._build_content(raw)

    # ── 決定論的分類ロジック ─────────────────────────────────────────────────

    def _classify_engagement(
        self, mapped: dict
    ) -> tuple[EngagementLevel, str, dict[str, str]]:
        """EngagementLevel を決定論的ルールで分類する（AI 不使用）。

        優先度:
        1. 判定 = A、またはメモに「アポ」を含む → APPOINTMENT_BOOKED
        2. 判定 = B、または温度感がホット/高 → HIGH_INTENT
        3. それ以外 → NURTURING
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

    def _match_products(self, text: str) -> tuple[list[ProductCode], list[str]]:
        """ProductCode の名寄せ。キーワードマッチで判定する（AI 不使用）。

        Returns:
            (マッチした ProductCode 群, マッチしたキーワード群)
        """
        text_lower = text.lower()
        codes: list[ProductCode] = []
        matched: list[str] = []

        a_hits = [kw for kw in _PRODUCT_A_KEYWORDS if kw.lower() in text_lower]
        b_hits = [kw for kw in _PRODUCT_B_KEYWORDS if kw.lower() in text_lower]

        if a_hits:
            codes.append(ProductCode.PRODUCT_A)
            matched.extend(a_hits)
        if b_hits:
            codes.append(ProductCode.PRODUCT_B)
            matched.extend(b_hits)

        return codes, matched
