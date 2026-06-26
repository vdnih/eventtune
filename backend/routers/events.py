"""
Events Router — /api/events

イベントの一覧取得と作成のみ提供する。
詳細・KPI・費用・アンケートの個別取得は廃止（新オントロジーでは Event に畳み込み済み）。
フロントエンドはデータ閲覧を /api/data エンドポイントで行う。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """登録されているすべてのイベントを返す。アップロードモーダルのイベント選択に使用。"""
    docs = space.col("events").get()
    events = [d.to_dict() for d in docs]
    events.sort(key=lambda e: e.get("event_date", ""), reverse=True)
    return {"events": events, "count": len(events)}


@router.post("", status_code=201)
async def create_event(
    body: CreateEventRequest,
    space: SpaceContext = Depends(get_space_context),
):
    """新規イベントを作成する。アップロードフロー内で使用。"""
    now = _now_iso()
    event_id = f"event_{uuid.uuid4().hex[:12]}"
    event_doc = {
        "event_id": event_id,
        "space_id": space.space_id,
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
        "appeal_summary": "",
        "appeal_vector": [],
        "created_at": now,
        "updated_at": now,
    }
    space.col("events").document(event_id).set(event_doc)
    return event_doc
