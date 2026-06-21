"""
Events Router — /api/events

Event エンティティの CRUD と、KPI / Survey / Costs の読み取りエンドポイント。
MarketingAgent のツールが内部的に呼び出す Firestore を直接参照するが、
フロントエンドの Sources パネル用に HTTP エンドポイントも提供する。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_space_context
from ontology import CostSummary, EventStatus, EventType
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Event CRUD ───────────────────────────────────────────────────────────────

class CreateEventRequest(BaseModel):
    name: str
    event_type: str = "展示会"
    status: str = "計画中"
    venue: str = ""
    event_date: str
    event_date_end: str
    booth_number: str | None = None
    total_budget: float = 0.0
    target_contact_count: int = 0
    description: str = ""


@router.get("")
async def list_events(space: SpaceContext = Depends(get_space_context)):
    """登録されているすべてのイベントを返す。Sources パネルの一覧表示に使用。"""
    docs = space.col("events").get()
    events = [d.to_dict() for d in docs]
    # 日付降順でソート
    events.sort(key=lambda e: e.get("event_date", ""), reverse=True)
    return {"events": events, "count": len(events)}


@router.get("/summary")
async def events_summary(space: SpaceContext = Depends(get_space_context)):
    """全イベントの横断サマリを返す。イベント未選択時の右ペイン表示に使用。

    各イベントごとに KPI（来場・名刺）と費用合計を集計し、全体の合計値も付与する。
    """
    event_docs = space.col("events").get()
    events_data = [d.to_dict() for d in event_docs]
    events_data.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    rows = []
    total_cost = 0.0
    total_visitors = 0
    total_contacts = 0
    for ev in events_data:
        event_id = ev.get("event_id")

        kpi_docs = space.col(f"events/{event_id}/kpi").get()
        kpi = kpi_docs[0].to_dict() if kpi_docs else None
        visitors = (kpi or {}).get("total_visitors_to_booth", 0)
        contacts = (kpi or {}).get("total_contacts_collected", 0)

        cost_docs = space.col(f"events/{event_id}/costs").get()
        cost_total = sum(c.to_dict().get("amount_jpy", 0) for c in cost_docs)

        total_cost += cost_total
        total_visitors += visitors
        total_contacts += contacts

        rows.append({
            "event_id": event_id,
            "name": ev.get("name"),
            "event_date": ev.get("event_date"),
            "status": ev.get("status"),
            "event_type": ev.get("event_type"),
            "total_visitors_to_booth": visitors,
            "total_contacts_collected": contacts,
            "cost_total_jpy": cost_total,
        })

    return {
        "events": rows,
        "totals": {
            "event_count": len(rows),
            "total_cost_jpy": total_cost,
            "total_visitors": total_visitors,
            "total_contacts": total_contacts,
        },
    }


@router.post("", status_code=201)
async def create_event(
    body: CreateEventRequest,
    space: SpaceContext = Depends(get_space_context),
):
    """新規イベントを作成する。"""
    now = _now_iso()
    event_id = f"event_{uuid.uuid4().hex[:12]}"
    event_doc = {
        "event_id": event_id,
        "name": body.name,
        "event_type": body.event_type,
        "status": body.status,
        "venue": body.venue,
        "event_date": body.event_date,
        "event_date_end": body.event_date_end,
        "booth_number": body.booth_number,
        "total_budget": body.total_budget,
        "target_contact_count": body.target_contact_count,
        "description": body.description,
        "created_at": now,
        "updated_at": now,
    }
    space.col("events").document(event_id).set(event_doc)
    return event_doc


@router.get("/{event_id}")
async def get_event(event_id: str, space: SpaceContext = Depends(get_space_context)):
    """指定したイベントの詳細を返す。"""
    doc = space.col("events").document(event_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    return doc.to_dict()


@router.put("/{event_id}")
async def update_event(
    event_id: str,
    body: dict,
    space: SpaceContext = Depends(get_space_context),
):
    """イベントの情報を更新する。"""
    ref = space.col("events").document(event_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    body["updated_at"] = _now_iso()
    body.pop("event_id", None)
    ref.update(body)
    return {**doc.to_dict(), **body}


@router.delete("/{event_id}", status_code=204)
async def delete_event(event_id: str, space: SpaceContext = Depends(get_space_context)):
    """イベント本体と配下の全サブコレクション（kpi/survey/costs/reports/
    batches/contacts）を再帰削除する。

    integration_batches / data_lineage は event_id 参照のメタデータとして残る
    （削除済みイベントを指すダングリングは許容）。
    """
    ref = space.col("events").document(event_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Event not found")
    space.db.recursive_delete(ref)
    return None


# ── KPI ──────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/kpi")
async def get_event_kpi(event_id: str, space: SpaceContext = Depends(get_space_context)):
    """指定したイベントの KPI を返す。"""
    docs = space.col(f"events/{event_id}/kpi").get()
    kpis = [d.to_dict() for d in docs]
    return {"kpi": kpis[0] if kpis else None}


# ── Survey ────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/survey")
async def get_event_survey(event_id: str, space: SpaceContext = Depends(get_space_context)):
    """指定したイベントのアンケート集計を返す。"""
    docs = space.col(f"events/{event_id}/survey").get()
    surveys = [d.to_dict() for d in docs]
    return {"survey": surveys[0] if surveys else None}


# ── Costs ─────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/costs")
async def get_event_costs(event_id: str, space: SpaceContext = Depends(get_space_context)):
    """指定したイベントの費用明細と集計を返す。"""
    docs = space.col(f"events/{event_id}/costs").get()
    costs = [d.to_dict() for d in docs]
    total = sum(c.get("amount_jpy", 0) for c in costs)
    by_category: dict[str, float] = {}
    for c in costs:
        cat = c.get("category", "その他")
        by_category[cat] = by_category.get(cat, 0) + c.get("amount_jpy", 0)
    summary = CostSummary(total_jpy=total, by_category=by_category)
    return {"costs": costs, "summary": summary.model_dump()}
