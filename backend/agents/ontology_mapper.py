"""
OntologyMapper — 取り込みの「解釈」フェーズ（決定論 Python・I/O なし）

DataIntegrationAgent (AI) が出力した生データ（列写像 / 文書抽出）を、決定論的ロジックで
**中間レコード**へ解釈する。最終的なオントロジーエンティティ（UUID 採番・永続）は作らない。

ADR-011 / docs/INGESTION_MAPPING.md に従う:
- 参加者ファイルの1行は person ではなく「接客(encounter)＝観測」。1行 → PersonObservation。
- マスタ（events/accounts/products/contents）は観測から conform される派生ディメンション。
  ここではリンク先を「名前」のまま持ち、PK の採番・名寄せ（find-or-create）は後段（conform/bind）が
  実在エンティティへの検索で行う。安定 ID（名前ハッシュ）は廃止。
- _normalize_name は照合キーの比較に使う（ID 生成には使わない）。
- 接客事実（接客担当・課題感・メモ）は PersonObservation 経由で EventAttendance へ。
  Person.appeal_summary は後段の導出ステージで全 attendance から集約生成する。

Auditable AI（原則4）に従い、各変換判定の根拠を TransformDecision / EntityTransformation として返す。
"""

import re
import unicodedata
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from ontology import (
    ColumnMappingResult,
    ContentType,
    CostCategory,
    DocumentExtractionResult,
    EngagementLevel,
    EntityTransformation,
    EventStatus,
    EventType,
    SkippedRecord,
    TransformDecision,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(s: str) -> str:
    """照合キー用の正規化: NFKC（全角半角統一）→ 全空白除去 → lower。

    表記揺れ（全角/半角・空白・大小）を吸収して同一マスタへ畳むための比較キー。
    ID 生成には使わない（PK は UUID）。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


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


# ── 中間レコード（PK 未確定・リンクは“名”のまま）─────────────────────────────────

@dataclass
class PersonObservation:
    """参加者ファイルの1行＝1接客の観測。person/account/attendance/interest の素。

    リンク先（event/account/product）は名前のまま持つ。PK は後段の bind が
    実在検索 find-or-create で採番する。
    """
    name: str
    email: str = ""
    company_name: str = ""          # account リンク名
    department: str = ""
    job_title: str = ""
    engagement_level: EngagementLevel | None = None
    event_link_name: str = ""       # event リンク名
    action_type: str = "参加"
    product_link_names: list[str] = field(default_factory=list)
    # 接客事実（→ EventAttendance）
    owner_staff: str = ""
    challenge_note: str = ""
    memo: str = ""
    # 監査
    source_label: str = ""
    decisions: list[TransformDecision] = field(default_factory=list)


@dataclass
class InterpretedRecord:
    """マスタ／費用などの解釈済みレコード。payload はエンティティ構築用の素（PK 抜き）。

    kind: "events" | "accounts" | "products" | "contents" | "cost_items" | "event_patch"
    name: マスタの自然キー（resolver の照合に使う）。cost_items / event_patch は未使用。
    links: 依存リンクの“名” {kind: name}（例 {"event": "2025秋展示会"}）。
    """
    kind: str
    payload: dict = field(default_factory=dict)
    name: str = ""
    links: dict[str, str] = field(default_factory=dict)
    transform: EntityTransformation | None = None


@dataclass
class MapResult:
    """map_* の戻り値。最終エンティティではなく中間レコード群を返す。"""
    person_observations: list[PersonObservation] = field(default_factory=list)
    records: list[InterpretedRecord] = field(default_factory=list)
    transformations: list[EntityTransformation] = field(default_factory=list)
    skipped: list[SkippedRecord] = field(default_factory=list)


class OntologyMapper:
    """AI 抽出結果 → 中間レコード への決定論的解釈を担う（純粋・I/O なし）。"""

    # ── パスA: 表形式データ ──────────────────────────────────────────────────

    def map_rows(
        self,
        column_mapping: ColumnMappingResult,
        rows: list[dict],
        space_id: str = "",
        job_id: str | None = None,
    ) -> MapResult:
        """CSV/Excel の行データを ColumnMappingResult に従い中間レコードへ解釈する。"""
        result = MapResult()
        entity_type = column_mapping.entity_type
        link_columns = column_mapping.link_columns or {}
        default_links = column_mapping.default_links or {}

        for row in rows:
            mapped = self._apply_column_map(column_mapping.column_map, row)
            if entity_type in ("persons", "contacts"):
                self._decompose_person(mapped, link_columns, default_links, result)
            elif entity_type == "events":
                self._push(result, self._build_event(mapped))
            elif entity_type == "accounts":
                self._push(result, self._build_account(mapped))
            elif entity_type == "products":
                self._push(result, self._build_product(mapped))
            elif entity_type == "contents":
                event_name = self._resolve_link_name("event", mapped, link_columns, default_links)
                self._push(result, self._build_content(mapped, event_name=event_name))
            elif entity_type == "cost_items":
                event_name = self._resolve_link_name("event", mapped, link_columns, default_links)
                self._push(result, self._build_cost_item(mapped, event_name=event_name))
        return result

    def _apply_column_map(self, column_map: dict[str, str], row: dict) -> dict:
        out: dict = {}
        for csv_col, ontology_field in column_map.items():
            val = row.get(csv_col, "")
            if val is None:
                val = ""
            out[ontology_field] = str(val).strip()
        out["__raw"] = row  # 元の行 dict = observation（一過性・永続しない）
        return out

    @staticmethod
    def _push(result: MapResult, build: "tuple[InterpretedRecord | None, SkippedRecord | None]") -> None:
        record, skip = build
        if record is not None:
            result.records.append(record)
            if record.transform is not None:
                result.transformations.append(record.transform)
        if skip is not None:
            result.skipped.append(skip)

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
        result: MapResult,
    ) -> None:
        """1行（1接客の観測）を PersonObservation へ解釈する。"""
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

        # 接客メモ集約（→ EventAttendance.memo）
        memo_sources = {
            "__memo": mapped.get("__memo", ""),
            "__needs": mapped.get("__needs", ""),
            "__caution": mapped.get("__caution", ""),
        }
        merged_fields = [k for k, v in memo_sources.items() if v]
        memo = " / ".join(memo_sources[k] for k in merged_fields)
        if merged_fields:
            decisions.append(TransformDecision(
                field="memo", value=memo,
                reason="次のフィールドを接客メモへ集約: " + ", ".join(merged_fields),
                source_signals={k: memo_sources[k] for k in merged_fields},
            ))

        company_name = (
            mapped.get("company_name", "").strip()
            or self._resolve_link_name("account", mapped, link_columns, default_links)
        )
        event_name = self._resolve_link_name("event", mapped, link_columns, default_links)

        # 製品リンクは link 列と __product_signal の両方から拾い得る。同じ列を指すと
        # 二重化するため、ソースごとに分割してから名称で重複排除する。
        product_names: list[str] = []
        for src in (
            self._resolve_link_name("product", mapped, link_columns, default_links),
            mapped.get("__product_signal", ""),
        ):
            for nm in _split_names(src):
                if nm not in product_names:
                    product_names.append(nm)
        if product_names:
            decisions.append(TransformDecision(
                field="interested_products", value=", ".join(product_names),
                reason="製品名を products リンクとして抽出（後段で名寄せ）",
                source_signals={"products": ", ".join(product_names)},
            ))

        # 接客担当・課題感（→ EventAttendance）。__challenge は旧 extracted_challenge も拾う。
        owner_staff = mapped.get("__event_owner", "").strip() or mapped.get("owner_staff", "").strip()
        challenge_note = (
            mapped.get("__challenge", "").strip()
            or mapped.get("extracted_challenge", "").strip()
        )

        result.person_observations.append(PersonObservation(
            name=name,
            email=mapped.get("email", "").strip(),
            company_name=company_name,
            department=mapped.get("department", ""),
            job_title=mapped.get("job_title", ""),
            engagement_level=engagement,
            event_link_name=event_name,
            product_link_names=product_names,
            owner_staff=owner_staff,
            challenge_note=challenge_note,
            memo=memo,
            source_label=f"{name}（{company_name}）" if company_name else name,
            decisions=decisions,
        ))

    # ── パスB: 非構造化ドキュメント ─────────────────────────────────────────

    def map_extraction(
        self,
        extraction: DocumentExtractionResult,
        space_id: str = "",
        job_id: str | None = None,
    ) -> MapResult:
        """DocumentExtractor の出力を中間レコードへ解釈する。"""
        result = MapResult()
        primary_event_name: str | None = None

        for event_data in extraction.events:
            record, skip = self._build_event(event_data)
            self._push(result, (record, skip))
            if record is not None and primary_event_name is None:
                primary_event_name = record.name

        # KPI / Survey は当該ドキュメントのイベントへ event_patch として束ねる（名で解決）
        if extraction.event_kpi and primary_event_name:
            self._push(result, self._build_event_kpi_patch(extraction.event_kpi, primary_event_name))
        if extraction.survey_response and primary_event_name:
            self._push(result, self._build_survey_patch(extraction.survey_response, primary_event_name))
        if extraction.cost_items and primary_event_name:
            for raw_cost in extraction.cost_items:
                self._push(result, self._build_cost_item(raw_cost, event_name=primary_event_name))
        if extraction.content_assets:
            for raw_asset in extraction.content_assets:
                self._push(result, self._build_content(raw_asset, event_name=primary_event_name or ""))

        return result

    # ── ビルダーメソッド（→ InterpretedRecord）─────────────────────────────────

    def _build_event(self, raw: dict) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        now = _now_iso()
        name = (raw.get("name") or "").strip()
        if not name:
            return None, SkippedRecord(
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

        payload = {
            "name": name,
            "event_type": event_type,
            "status": status,
            "venue": raw.get("venue") or "",
            "event_date": raw.get("event_date") or "",
            "event_date_end": raw.get("event_date_end") or raw.get("event_date") or "",
            "booth_number": raw.get("booth_number") or None,
            "total_budget": _to_float(raw.get("total_budget")),
            "target_contact_count": _to_int(raw.get("target_contact_count")),
            "description": raw.get("description") or "",
            "created_at": now,
            "updated_at": now,
        }
        transform = EntityTransformation(
            entity_type="Event", entity_id=name, source_label=name,
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
        return InterpretedRecord(kind="events", payload=payload, name=name, transform=transform), None

    def _build_account(self, raw: dict) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        name = (raw.get("company_name") or raw.get("account_name") or "").strip()
        if not name:
            return None, SkippedRecord(
                entity_type="Account", reason="account_name 空のためスキップ", detail="",
            )
        payload = {
            "account_name": name,
            "industry_type": raw.get("industry_type") or "",
            "company_size": raw.get("company_size") or "",
            "created_at": _now_iso(),
        }
        transform = EntityTransformation(
            entity_type="Account", entity_id=name, source_label=name, decisions=[],
        )
        return InterpretedRecord(kind="accounts", payload=payload, name=name, transform=transform), None

    def _build_product(self, raw: dict) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        name = (raw.get("product_name") or raw.get("name") or "").strip()
        if not name:
            return None, SkippedRecord(
                entity_type="Product", reason="product_name 空のためスキップ", detail="",
            )
        payload = {
            "product_name": name,
            "product_category": raw.get("product_category") or "",
            "created_at": _now_iso(),
        }
        transform = EntityTransformation(
            entity_type="Product", entity_id=name, source_label=name, decisions=[],
        )
        return InterpretedRecord(kind="products", payload=payload, name=name, transform=transform), None

    def _build_event_kpi_patch(
        self, raw: dict, event_name: str
    ) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        """KPI を当該イベントへ畳み込む event_patch（None 値は除去）。"""
        payload = {
            "total_visitors_to_booth": _to_int(raw.get("total_visitors_to_booth")) or None,
            "total_contacts_collected": _to_int(raw.get("total_contacts_collected")) or None,
            "appointments_booked": _to_int(raw.get("appointments_booked")) or None,
            "demo_sessions_held": _to_int(raw.get("demo_sessions_held")) or None,
            "follow_email_open_rate": _to_float(raw.get("follow_email_open_rate")) or None,
            "follow_email_reply_rate": _to_float(raw.get("follow_email_reply_rate")) or None,
            "pipeline_value_jpy": _to_float(raw.get("pipeline_value_jpy")) or None,
            "closed_deals_3m": _to_int(raw.get("closed_deals_3m")) or None,
            "closed_revenue_3m_jpy": _to_float(raw.get("closed_revenue_3m_jpy")) or None,
            "updated_at": _now_iso(),
        }
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_name,
            source_label=f"KPI patch（event={event_name}）",
            decisions=[TransformDecision(
                field="pipeline_value_jpy", value=str(payload["pipeline_value_jpy"]),
                reason="数値化して Event に畳み込み",
                source_signals={"pipeline_value_jpy": str(raw.get("pipeline_value_jpy", ""))},
            )],
        )
        return InterpretedRecord(
            kind="event_patch", payload=payload, links={"event": event_name}, transform=transform,
        ), None

    def _build_survey_patch(
        self, raw: dict, event_name: str
    ) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        payload = {
            "nps_score": _to_float(raw.get("nps_score")) or None,
            "total_survey_responses": _to_int(raw.get("total_responses")) or None,
            "updated_at": _now_iso(),
        }
        transform = EntityTransformation(
            entity_type="Event", entity_id=event_name,
            source_label=f"Survey patch（event={event_name}）",
            decisions=[TransformDecision(
                field="nps_score", value=str(payload["nps_score"]),
                reason="NPS スコアを Event に畳み込み",
                source_signals={"nps_score": str(raw.get("nps_score", ""))},
            )],
        )
        return InterpretedRecord(
            kind="event_patch", payload=payload, links={"event": event_name}, transform=transform,
        ), None

    def _build_cost_item(
        self, raw: dict, event_name: str
    ) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        if not event_name:
            return None, SkippedRecord(
                entity_type="CostItem", reason="イベントリンク未解決のためスキップ",
                detail=f"description={raw.get('description','')!r}",
            )
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
            return None, SkippedRecord(
                entity_type="CostItem", reason="amount<=0 のためスキップ",
                detail=f"description={description!r} amount_raw={amount_raw!r}",
            )

        payload = {
            "category": category,
            "description": description,
            "amount_jpy": amount,
            "vendor_name": raw.get("vendor_name") or None,
            "invoice_date": raw.get("invoice_date") or None,
        }
        transform = EntityTransformation(
            entity_type="CostItem", entity_id=description or "cost",
            source_label=description or "cost",
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
        return InterpretedRecord(
            kind="cost_items", payload=payload, links={"event": event_name}, transform=transform,
        ), None

    def _build_content(
        self, raw: dict, event_name: str = ""
    ) -> "tuple[InterpretedRecord | None, SkippedRecord | None]":
        type_map = {v.value: v for v in ContentType}
        raw_type = raw.get("content_type", "")
        content_type = type_map.get(raw_type, ContentType.WHITE_PAPER)
        name = (raw.get("name") or raw.get("content_name") or "").strip()
        if not name:
            return None, SkippedRecord(
                entity_type="Content", reason="name 空のためスキップ",
                detail=f"content_type={raw_type!r}",
            )
        payload = {
            "content_name": name,
            "content_type": content_type,
            "url": raw.get("url") or "",
            "description": raw.get("description") or "",
        }
        links = {"event": event_name} if event_name else {}
        transform = EntityTransformation(
            entity_type="Content", entity_id=name, source_label=name,
            decisions=[TransformDecision(
                field="content_type", value=content_type.value,
                reason=(f"'{raw_type}' を enum にマッピング" if raw_type in type_map
                        else f"未知の値 '{raw_type}' → 既定(資料・ホワイトペーパー)"),
                source_signals={"content_type": str(raw_type)},
            )],
        )
        return InterpretedRecord(
            kind="contents", payload=payload, name=name, links=links, transform=transform,
        ), None

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
