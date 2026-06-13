"""
Marketing Router — /api/marketing

MarketingAgent とのチャット（SSE）と、メール生成ランの管理を担う。
"""

import asyncio
import json
import logging
import uuid

import pandas as pd
import io
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from firebase_admin import firestore
from pydantic import BaseModel

from dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketing", tags=["marketing"])


# ── チャット (SSE) ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: dict = Depends(get_current_user),
):
    """
    MarketingAgent とのチャット。Server-Sent Events でストリーミングする。
    各イベントは JSON 文字列として `data: {...}\n\n` 形式で送信される。
    """
    from agents.marketing_agent import chat_stream

    user_id = user.get("uid", "default_user")
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"

    async def event_generator():
        try:
            async for event in chat_stream(
                message=body.message,
                session_id=session_id,
                user_id=user_id,
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

async def _execute_run(run_id: str) -> None:
    from agents.marketing_agent import _execute_email_run
    try:
        await _execute_email_run(run_id)
    except Exception as e:
        logger.exception("email run failed: run_id=%s", run_id)
        try:
            firestore.client().collection("marketing_runs").document(run_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


@router.post("/runs/{run_id}/execute", status_code=202)
async def execute_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    compose_emails ツールが作成した run_id のメール生成を実際に実行する。
    MarketingAgent が compose_emails を呼んだ後、フロントエンドがこのエンドポイントを呼ぶ。
    """
    doc = firestore.client().collection("marketing_runs").document(run_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Run not found")

    data = doc.to_dict()
    if data.get("status") not in ("queued", "error"):
        raise HTTPException(status_code=400, detail=f"Run status is '{data.get('status')}', cannot re-execute")

    background_tasks.add_task(_execute_run, run_id)
    return {"run_id": run_id, "status": "processing"}


@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: str,
    user: dict = Depends(get_current_user),
):
    """メール生成ランの進捗を返す。"""
    doc = firestore.client().collection("marketing_runs").document(run_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Run not found")
    data = doc.to_dict()
    return {
        "run_id": run_id,
        "status": data.get("status"),
        "purpose": data.get("purpose"),
        "total": data.get("total", 0),
        "done": data.get("done", 0),
        "email_count": data.get("email_count", 0),
        "error": data.get("error"),
    }


@router.get("/runs/{run_id}/results")
async def get_run_results(
    run_id: str,
    user: dict = Depends(get_current_user),
):
    """生成されたメール一覧を返す。"""
    db = firestore.client()
    emails = [s.to_dict() for s in db.collection(f"marketing_runs/{run_id}/emails").get()]
    return {"emails": emails, "count": len(emails)}


@router.get("/runs/{run_id}/export")
async def export_run_results(
    run_id: str,
    user: dict = Depends(get_current_user),
):
    """生成されたメールを CSV でエクスポートする。"""
    db = firestore.client()
    emails = [s.to_dict() for s in db.collection(f"marketing_runs/{run_id}/emails").get()]
    if not emails:
        raise HTTPException(status_code=404, detail="メールがまだ生成されていません")

    rows = []
    for email in emails:
        blocks = email.get("blocks", [])
        full_text = "\n\n".join(b.get("block_text", "") for b in blocks)
        reasons = "\n".join(
            f"[{b.get('block_type','')}] {b.get('reason_for_inclusion','')}"
            for b in blocks
        )
        rows.append({
            "contact_id": email.get("contact_id", ""),
            "件名": email.get("subject", ""),
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
        headers={"Content-Disposition": f"attachment; filename=emails_{run_id[:8]}.csv"},
    )
