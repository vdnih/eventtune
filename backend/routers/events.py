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
from firebase_admin import firestore
from pydantic import BaseModel

from dependencies import get_current_user
from ontology import CostSummary, EventStatus, EventType

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
async def list_events(user: dict = Depends(get_current_user)):
    """登録されているすべてのイベントを返す。Sources パネルの一覧表示に使用。"""
    docs = firestore.client().collection("events").get()
    events = [d.to_dict() for d in docs]
    # 日付降順でソート
    events.sort(key=lambda e: e.get("event_date", ""), reverse=True)
    return {"events": events, "count": len(events)}


@router.post("", status_code=201)
async def create_event(
    body: CreateEventRequest,
    user: dict = Depends(get_current_user),
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
    firestore.client().collection("events").document(event_id).set(event_doc)
    return event_doc


@router.get("/{event_id}")
async def get_event(event_id: str, user: dict = Depends(get_current_user)):
    """指定したイベントの詳細を返す。"""
    doc = firestore.client().collection("events").document(event_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    return doc.to_dict()


@router.put("/{event_id}")
async def update_event(
    event_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    """イベントの情報を更新する。"""
    db = firestore.client()
    doc = db.collection("events").document(event_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    body["updated_at"] = _now_iso()
    body.pop("event_id", None)
    db.collection("events").document(event_id).update(body)
    return {**doc.to_dict(), **body}


# ── KPI ──────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/kpi")
async def get_event_kpi(event_id: str, user: dict = Depends(get_current_user)):
    """指定したイベントの KPI を返す。"""
    docs = firestore.client().collection(f"events/{event_id}/kpi").get()
    kpis = [d.to_dict() for d in docs]
    return {"kpi": kpis[0] if kpis else None}


# ── Survey ────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/survey")
async def get_event_survey(event_id: str, user: dict = Depends(get_current_user)):
    """指定したイベントのアンケート集計を返す。"""
    docs = firestore.client().collection(f"events/{event_id}/survey").get()
    surveys = [d.to_dict() for d in docs]
    return {"survey": surveys[0] if surveys else None}


# ── Costs ─────────────────────────────────────────────────────────────────────

@router.get("/{event_id}/costs")
async def get_event_costs(event_id: str, user: dict = Depends(get_current_user)):
    """指定したイベントの費用明細と集計を返す。"""
    docs = firestore.client().collection(f"events/{event_id}/costs").get()
    costs = [d.to_dict() for d in docs]
    total = sum(c.get("amount_jpy", 0) for c in costs)
    by_category: dict[str, float] = {}
    for c in costs:
        cat = c.get("category", "その他")
        by_category[cat] = by_category.get(cat, 0) + c.get("amount_jpy", 0)
    summary = CostSummary(total_jpy=total, by_category=by_category)
    return {"costs": costs, "summary": summary.model_dump()}
