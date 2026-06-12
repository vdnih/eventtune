import io
import logging

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from firebase_admin import firestore

from dependencies import get_current_user
from ontology import BlockType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execute"])


async def _run_execute(batch_id: str) -> None:
    from agents.execution_agent import generate_emails_for_batch

    try:
        await generate_emails_for_batch(batch_id)
    except Exception as e:
        logger.exception("generate_emails_for_batch failed for batch_id=%s: %s", batch_id, e)
        try:
            firestore.client().collection("batches").document(batch_id).update({
                "execution_status": "error",
                "execution_error": str(e)[:500],
            })
        except Exception as fe:
            logger.exception("failed to update Firestore execution error: %s", fe)


@router.post("/api/execute", status_code=202)
async def start_execute(
    background_tasks: BackgroundTasks,
    body: dict,
    user: dict = Depends(get_current_user),
):
    batch_id = body.get("batch_id")
    if not batch_id:
        raise HTTPException(status_code=400, detail="batch_id が必要です")

    db = firestore.client()
    doc = db.collection("batches").document(batch_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    data = doc.to_dict()
    if data.get("status") != "done":
        raise HTTPException(
            status_code=400,
            detail="取り込み完了前にメール生成は実行できません",
        )

    background_tasks.add_task(_run_execute, batch_id)
    return {"batch_id": batch_id, "message": "メール生成を開始しました"}


@router.get("/api/execute/{batch_id}/status")
async def get_execute_status(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    db = firestore.client()
    doc = db.collection("batches").document(batch_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    data = doc.to_dict()
    return {
        "batch_id": batch_id,
        "execution_status": data.get("execution_status", "not_started"),
        "execution_done": data.get("execution_done", 0),
        "email_count": data.get("email_count", 0),
        "lead_count": data.get("lead_count", 0),
        "execution_error": data.get("execution_error"),
    }


@router.get("/api/execute/{batch_id}/emails")
async def get_emails(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    db = firestore.client()
    emails_snap = (
        db.collection("emails")
        .where("batch_id", "==", batch_id)
        .get()
    )
    emails = [snap.to_dict() for snap in emails_snap]
    return {"emails": emails, "count": len(emails)}


@router.get("/api/execute/{batch_id}/download")
async def download_emails(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    db = firestore.client()

    # leads をlead_idでインデックス
    leads_snap = (
        db.collection("batches").document(batch_id).collection("leads").get()
    )
    leads_by_id = {
        snap.to_dict().get("lead_id", snap.id): snap.to_dict()
        for snap in leads_snap
    }

    emails_snap = (
        db.collection("emails")
        .where("batch_id", "==", batch_id)
        .get()
    )

    rows = []
    for snap in emails_snap:
        email = snap.to_dict()
        lead = leads_by_id.get(email.get("lead_id"), {})

        # blocksをblock_typeをキーに辞書化
        blocks_by_type = {b["block_type"]: b for b in email.get("blocks", [])}

        row = {
            "氏名": lead.get("name", ""),
            "会社名": lead.get("company_name", ""),
            "部署": lead.get("department", ""),
            "役職": lead.get("job_title", ""),
            "セグメント": lead.get("segment", ""),
            "興味製品": ", ".join(lead.get("interested_products", [])),
            "抽出課題": lead.get("extracted_challenge", ""),
            "件名": email.get("subject", ""),
        }
        # 各ブロックを列として追加
        for bt in BlockType:
            block = blocks_by_type.get(bt.value, {})
            row[f"[本文]{bt.value}"] = block.get("block_text", "")
            row[f"[理由]{bt.value}"] = block.get("reason_for_inclusion", "")

        rows.append(row)

    if not rows:
        raise HTTPException(status_code=404, detail="メールがまだ生成されていません")

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename=emails_{batch_id[:8]}.csv"},
    )
