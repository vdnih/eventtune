"""
Marketing Router — /api/marketing

MarketingAgent とのチャット（SSE）と、メール生成ランの管理を担う。
"""

import json
import logging
import uuid

import pandas as pd
import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketing", tags=["marketing"])


# ── チャット (SSE) ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    event_id: str | None = None


@router.post("/chat")
async def chat(
    body: ChatRequest,
    space: SpaceContext = Depends(get_space_context),
):
    """
    MarketingAgent とのチャット。Server-Sent Events でストリーミングする。
    各イベントは JSON 文字列として `data: {...}\n\n` 形式で送信される。
    """
    from agents.marketing_agent import chat_stream

    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"

    async def event_generator():
        try:
            async for event in chat_stream(
                message=body.message,
                session_id=session_id,
                space=space,
                event_id=body.event_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("chat SSE error")
            err_event = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


# ── メール生成ラン ─────────────────────────────────────────────────────────
# 組み立ては MarketingAgent の run_assembly ツールが決定論的に行い、run は作成時点で
# 完了済み（status=done）。フロントは状態取得・結果取得・CSVエクスポートのみ行う。


@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """メール生成ランの進捗を返す。"""
    doc = space.col("marketing_runs").document(run_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Run not found")
    data = doc.to_dict()
    return {
        "run_id": run_id,
        "status": data.get("status"),
        "purpose": data.get("purpose"),
        "total": data.get("total", 0),
        "done": data.get("done", 0),
        "deliverable_count": data.get("deliverable_count", 0),
        "snapshot_id": data.get("snapshot_id"),
        "error": data.get("error"),
    }


@router.get("/runs/{run_id}/results")
async def get_run_results(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """生成された成果物（Deliverable）一覧を返す。"""
    deliverables = [s.to_dict() for s in space.col(f"marketing_runs/{run_id}/deliverables").get()]
    return {"deliverables": deliverables, "count": len(deliverables)}


@router.get("/runs/{run_id}/export")
async def export_run_results(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """生成された成果物を CSV でエクスポートする。"""
    deliverables = [s.to_dict() for s in space.col(f"marketing_runs/{run_id}/deliverables").get()]
    if not deliverables:
        raise HTTPException(status_code=404, detail="成果物がまだ生成されていません")

    rows = []
    for dlv in deliverables:
        blocks = dlv.get("blocks", [])
        full_text = "\n\n".join(b.get("block_text", "") for b in blocks)
        reasons = "\n".join(
            f"[{b.get('block_type','')}] {b.get('reason_for_inclusion','')}"
            for b in blocks
        )
        rows.append({
            "person_id": dlv.get("person_id", ""),
            "bucket": dlv.get("bucket", ""),
            "件名": dlv.get("subject", ""),
            "本文（全体）": full_text,
            "包含根拠": reasons,
        })

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename=deliverables_{run_id[:8]}.csv"},
    )
