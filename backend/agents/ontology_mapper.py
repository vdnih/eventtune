"""
OntologyMapper — DataIntegrationPipeline の変換フェーズ

DataIntegrationAgent (AI) が出力した生データを、決定論的ロジックで
最終的なオントロジーエンティティに変換する。
EngagementLevel 判定・Product 名寄せなど、ビジネスルールはここに集約する。
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from ontology import (
    ColumnMappingResult,
    Contact,
    ContactStage,
    ContentAsset,
    ContentType,
    CostCategory,
    CostItem,
    DocumentExtractionResult,
    EngagementCounts,
    EngagementLevel,
    EntityTransformation,
    Event,
    EventKPI,
    EventStatus,
    EventType,
    Product,
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


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
    ) -> MapResult:
        """CSV/Excel の行データを、ColumnMappingResult に従いエンティティに変換する。"""
        results: list[Any] = []
        transforms: list[EntityTransformation] = []
        skipped: list[SkippedRecord] = []
        for row in rows:
            mapped = self._apply_column_map(column_mapping.column_map, row)
            if column_mapping.entity_type == "contacts":
                entity, transform, skip = self._build_contact(
                    mapped, event_id=event_id, batch_id=batch_id
                )
            elif column_mapping.entity_type == "cost_items":
                entity, transform, skip = self._build_cost_item(mapped, event_id=event_id or "")
            else:
                continue
            if entity is not None:
                results.append(entity)
            if transform is not None:
                transforms.append(transform)
            if skip is not None:
                skipped.append(skip)
        return results, transforms, skipped

    def _apply_column_map(self, column_map: dict[str, str], row: dict) -> dict:
        """行データのキーをカラムマップに従って変換する。"""
        out: dict = {}
        for csv_col, ontology_field in column_map.items():
            val = row.get(csv_col, "")
            if val is None:
                val = ""
            out[ontology_field] = str(val).strip()
        # 元の生の値もシグナル用に保持
        out["__raw"] = row
        return out

    def _build_contact(
        self,
        mapped: dict,
        event_id: str | None = None,
        batch_id: str | None = None,
    ) -> BuildResult:
        name_last = mapped.get("name_last", "")
        name_first = mapped.get("name_first", "")
        name = mapped.get("name", f"{name_last}{name_first}").strip()
        if not name:
            return None, None, SkippedRecord(
                entity_type="Contact",
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
        products, matched_keywords = self._match_products(product_text)
        decisions.append(TransformDecision(
            field="interested_products",
            value=", ".join(p.value for p in products) or "（なし）",
            reason=(
                "キーワードマッチ: " + ", ".join(matched_keywords)
                if matched_keywords else "マッチするキーワードなし"
            ),
            source_signals={"__product_signal": mapped.get("__product_signal", "")},
        ))

        # 担当者所感・メモを notes に集約
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

        contact = Contact(
            contact_id=_new_id("contact_"),
            name=name,
            company_name=mapped.get("company_name", ""),
            department=mapped.get("department", ""),
            job_title=mapped.get("job_title", ""),
            email=mapped.get("email") or None,
            stage=ContactStage.LEAD,
            engagement_level=engagement,
            interested_products=products,
            extracted_challenge=mapped.get("extracted_challenge", ""),
            notes=notes,
            source_event_id=event_id,
        )
        transform = EntityTransformation(
            entity_type="Contact",
            entity_id=contact.contact_id,
            source_label=f"{name}（{contact.company_name}）" if contact.company_name else name,
            decisions=decisions,
        )
        return contact, transform, None

    # ── パスB: 非構造化ドキュメント ─────────────────────────────────────────

    def map_extraction(
        self,
        extraction: DocumentExtractionResult,
        event_id: str | None = None,
        batch_id: str | None = None,
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

        if extraction.event:
            event = _collect(self._build_event(extraction.event, event_id=event_id))
            if event is not None and resolved_event_id is None:
                resolved_event_id = event.event_id

        if extraction.event_kpi and resolved_event_id:
            _collect(self._build_event_kpi(extraction.event_kpi, event_id=resolved_event_id))

        if extraction.cost_items and resolved_event_id:
            for raw_cost in extraction.cost_items:
                _collect(self._build_cost_item(raw_cost, event_id=resolved_event_id))

        if extraction.survey_response and resolved_event_id:
            _collect(self._build_survey_response(
                extraction.survey_response, event_id=resolved_event_id
            ))

        if extraction.content_assets:
            for raw_asset in extraction.content_assets:
                _collect(self._build_content_asset(raw_asset))

        return results, transforms, skipped

    # ── ビルダーメソッド ─────────────────────────────────────────────────────

    def _build_event(self, raw: dict, event_id: str | None = None) -> BuildResult:
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
        budget = float(raw.get("total_budget", 0))

        event = Event(
            event_id=event_id or raw.get("event_id") or _new_id("event_"),
            name=raw.get("name", ""),
            event_type=event_type,
            status=status,
            venue=raw.get("venue", ""),
            event_date=raw.get("event_date", ""),
            event_date_end=raw.get("event_date_end", raw.get("event_date", "")),
            booth_number=raw.get("booth_number") or None,
            total_budget=budget,
            target_contact_count=int(raw.get("target_contact_count", 0)),
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

    def _build_event_kpi(self, raw: dict, event_id: str) -> BuildResult:
        by_engagement = raw.get("contacts_by_engagement", {})
        kpi = EventKPI(
            kpi_id=_new_id("kpi_"),
            event_id=event_id,
            total_visitors_to_booth=int(raw.get("total_visitors_to_booth", 0)),
            total_contacts_collected=int(raw.get("total_contacts_collected", 0)),
            contacts_by_engagement=EngagementCounts(
                appointment_booked=int(by_engagement.get("appointment_booked", 0)),
                high_intent=int(by_engagement.get("high_intent", 0)),
                nurturing=int(by_engagement.get("nurturing", 0)),
            ),
            appointments_booked=int(raw.get("appointments_booked", 0)),
            demo_sessions_held=int(raw.get("demo_sessions_held", 0)),
            follow_email_open_rate=float(raw.get("follow_email_open_rate", 0.0)),
            follow_email_reply_rate=float(raw.get("follow_email_reply_rate", 0.0)),
            pipeline_value_jpy=float(raw.get("pipeline_value_jpy", 0)),
            closed_deals_3m=int(raw.get("closed_deals_3m", 0)),
            closed_revenue_3m_jpy=float(raw.get("closed_revenue_3m_jpy", 0)),
            created_at=_now_iso(),
        )
        decisions = [
            TransformDecision(
                field="follow_email_open_rate", value=str(kpi.follow_email_open_rate),
                reason="小数（0.0〜1.0）として数値化",
                source_signals={"follow_email_open_rate": str(raw.get("follow_email_open_rate", ""))},
            ),
            TransformDecision(
                field="pipeline_value_jpy", value=str(kpi.pipeline_value_jpy),
                reason="数値化",
                source_signals={"pipeline_value_jpy": str(raw.get("pipeline_value_jpy", ""))},
            ),
        ]
        transform = EntityTransformation(
            entity_type="EventKPI", entity_id=kpi.kpi_id,
            source_label=f"KPI（event={event_id}）", decisions=decisions,
        )
        return kpi, transform, None

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

    def _build_survey_response(self, raw: dict, event_id: str) -> BuildResult:
        cat_map = {v.value: v for v in SatisfactionCategory}
        scores = []
        for s in raw.get("satisfaction_scores", []):
            cat = cat_map.get(s.get("category", ""), SatisfactionCategory.OVERALL)
            scores.append(
                SatisfactionScore(
                    category=cat,
                    avg_score=float(s.get("avg_score", 0)),
                    response_count=int(s.get("response_count", 0)),
                )
            )
        survey = SurveyResponse(
            survey_id=_new_id("survey_"),
            event_id=event_id,
            total_responses=int(raw.get("total_responses", 0)),
            nps_score=float(raw.get("nps_score", 0)),
            nps_promoters=int(raw.get("nps_promoters", 0)),
            nps_passives=int(raw.get("nps_passives", 0)),
            nps_detractors=int(raw.get("nps_detractors", 0)),
            satisfaction_scores=scores,
            verbatim_positives=raw.get("verbatim_positives") or [],
            verbatim_negatives=raw.get("verbatim_negatives") or [],
            verbatim_suggestions=raw.get("verbatim_suggestions") or [],
            created_at=_now_iso(),
        )
        decisions = [TransformDecision(
            field="satisfaction_scores",
            value=f"{len(scores)} カテゴリを正規化",
            reason="各 category を SatisfactionCategory enum にマッピング（未知は総合満足度）",
            source_signals={"raw_categories": ", ".join(
                s.get("category", "") for s in raw.get("satisfaction_scores", [])
            )},
        )]
        transform = EntityTransformation(
            entity_type="SurveyResponse", entity_id=survey.survey_id,
            source_label=f"アンケート（event={event_id}）", decisions=decisions,
        )
        return survey, transform, None

    def _build_content_asset(self, raw: dict) -> BuildResult:
        type_map = {v.value: v for v in ContentType}
        raw_type = raw.get("content_type", "")
        content_type = type_map.get(raw_type, ContentType.WHITE_PAPER)
        if not raw.get("name"):
            return None, None, SkippedRecord(
                entity_type="ContentAsset",
                reason="name 空のためスキップ",
                detail=f"content_type={raw_type!r}",
            )
        asset = ContentAsset(
            asset_id=raw.get("asset_id") or _new_id("asset_"),
            content_type=content_type,
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            url=raw.get("url", ""),
            linked_event_id=raw.get("linked_event_id") or None,
        )
        decisions = [TransformDecision(
            field="content_type", value=content_type.value,
            reason=(f"'{raw_type}' を enum にマッピング" if raw_type in type_map
                    else f"未知の値 '{raw_type}' → 既定(資料・ホワイトペーパー)"),
            source_signals={"content_type": str(raw_type)},
        )]
        transform = EntityTransformation(
            entity_type="ContentAsset", entity_id=asset.asset_id,
            source_label=asset.name, decisions=decisions,
        )
        return asset, transform, None

    # ── 決定論的分類ロジック ─────────────────────────────────────────────────

    def _classify_engagement(
        self, mapped: dict
    ) -> tuple[EngagementLevel, str, dict[str, str]]:
        """
        EngagementLevel を決定論的ルールで分類する。
        AIに委ねずコードで明示することでデータリネージを担保する。

        Returns:
            (判定結果, 判定理由, 判定に使った生シグナル) — Auditable AI のため理由を必ず返す。

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

        # アポ獲得済み判定
        if judgment == "A":
            return EngagementLevel.APPOINTMENT_BOOKED, "判定シグナル=A", signals
        if any(kw in memo for kw in ("アポ", "アポイント", "アポ取得", "アポ設定")):
            return EngagementLevel.APPOINTMENT_BOOKED, "memo に「アポ」を含む", signals

        # 感度高判定
        if judgment == "B":
            return EngagementLevel.HIGH_INTENT, "判定シグナル=B", signals
        if temperature in ("ホット", "ウォーム", "高", "中", "hot", "warm"):
            return EngagementLevel.HIGH_INTENT, f"温度感シグナル={temperature}", signals

        return EngagementLevel.NURTURING, "該当ルールなし → 既定(通常リード)", signals

    def _match_products(self, text: str) -> tuple[list[Product], list[str]]:
        """Product の名寄せ。キーワードマッチで判定する（AI不使用）。

        Returns:
            (マッチした Product 群, マッチしたキーワード群) — 根拠記録のためキーワードも返す。
        """
        text_lower = text.lower()
        products: list[Product] = []
        matched: list[str] = []

        a_hits = [kw for kw in _PRODUCT_A_KEYWORDS if kw.lower() in text_lower]
        b_hits = [kw for kw in _PRODUCT_B_KEYWORDS if kw.lower() in text_lower]

        if a_hits:
            products.append(Product.PRODUCT_A)
            matched.extend(a_hits)
        if b_hits:
            products.append(Product.PRODUCT_B)
            matched.extend(b_hits)

        return products, matched
