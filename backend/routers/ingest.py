import io
import logging
import uuid

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from firebase_admin import firestore

from dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


async def _run_ingest(batch_id: str, df: pd.DataFrame, filename: str) -> None:
    from agents.ingestion_agent import ingest_dataframe

    try:
        await ingest_dataframe(df, batch_id, filename)
    except Exception as e:
        logger.exception("ingest_dataframe failed for batch_id=%s: %s", batch_id, e)
        try:
            firestore.client().collection("batches").document(batch_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception as fe:
            logger.exception("failed to update Firestore error status: %s", fe)


@router.post("/api/ingest", status_code=202)
async def start_ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    content = await file.read()
    filename = file.filename or "upload"
    df: pd.DataFrame | None = None

    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(io.BytesIO(content))
        except Exception:
            raise HTTPException(status_code=400, detail="Excelファイルの読み込みに失敗しました")
    else:
        for encoding in ("utf-8-sig", "utf-8", "cp932"):
            try:
                df = pd.read_csv(io.BytesIO(content), encoding=encoding)
                break
            except Exception:
                continue

    if df is None:
        raise HTTPException(status_code=400, detail="ファイルの読み込みに失敗しました")
    if len(df) == 0:
        raise HTTPException(status_code=400, detail="データがありません")

    batch_id = str(uuid.uuid4())
    background_tasks.add_task(_run_ingest, batch_id, df, filename)

    return {"batch_id": batch_id, "total": len(df)}


@router.get("/api/batches/{batch_id}")
async def get_batch_status(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    db = firestore.client()
    doc = db.collection("batches").document(batch_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    data = doc.to_dict()

    leads_snap = (
        db.collection("batches").document(batch_id).collection("leads").get()
    )
    leads = [snap.to_dict() for snap in leads_snap]

    segment_counts: dict[str, int] = {}
    for lead in leads:
        seg = lead.get("segment", "不明")
        segment_counts[seg] = segment_counts.get(seg, 0) + 1

    return {
        "batch_id": batch_id,
        "status": data.get("status"),
        "filename": data.get("filename"),
        "total": data.get("row_count"),
        "lead_count": data.get("lead_count", len(leads)),
        "segment_counts": segment_counts,
        "error": data.get("error"),
    }


@router.get("/api/batches/{batch_id}/leads")
async def get_batch_leads(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    db = firestore.client()
    leads_snap = (
        db.collection("batches").document(batch_id).collection("leads").get()
    )
    leads = [snap.to_dict() for snap in leads_snap]
    return {"leads": leads, "count": len(leads)}
