"""
Data Integration Router — /api/integration/batches

ファイルアップロードを受け取り、DataIntegrationAgent でオントロジーに変換する。
CSV/Excel はパスA（スキーママッピング）、テキストはパスB（ドキュメント抽出）で処理する。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from firebase_admin import firestore

from dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_integration(
    batch_id: str,
    filename: str,
    content: bytes,
    event_id: str | None,
) -> None:
    from agents.data_integration_agent import process_file

    db = firestore.client()
    try:
        db.collection("integration_batches").document(batch_id).update({"status": "processing"})
        entities, lineage = await process_file(filename, content, event_id, batch_id, db)

        created_summary = {k: len(v) for k, v in lineage.created_entity_ids.items()}
        db.collection("integration_batches").document(batch_id).update({
            "status": "done",
            "created_entities": created_summary,
            "lineage_id": lineage.lineage_id,
        })
        logger.info("integration done: batch_id=%s created=%s", batch_id, created_summary)

    except Exception as e:
        logger.exception("integration failed: batch_id=%s error=%s", batch_id, e)
        try:
            db.collection("integration_batches").document(batch_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    event_id: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """ファイルをアップロードしてデータ統合バッチを開始する。"""
    content = await file.read()
    filename = file.filename or "upload"

    if not content:
        raise HTTPException(status_code=400, detail="ファイルが空です")

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"

    firestore.client().collection("integration_batches").document(batch_id).set({
        "batch_id": batch_id,
        "filename": filename,
        "event_id": event_id,
        "status": "queued",
        "created_at": _now_iso(),
    })

    background_tasks.add_task(_run_integration, batch_id, filename, content, event_id)

    return {"batch_id": batch_id, "filename": filename}


@router.get("/batches/{batch_id}")
async def get_batch_status(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    """バッチの処理状況と生成されたエンティティ数を返す。"""
    doc = firestore.client().collection("integration_batches").document(batch_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")
    data = doc.to_dict()
    return {
        "batch_id": batch_id,
        "status": data.get("status"),
        "filename": data.get("filename"),
        "event_id": data.get("event_id"),
        "created_entities": data.get("created_entities", {}),
        "error": data.get("error"),
    }


@router.get("/batches/{batch_id}/report")
async def get_batch_report(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    """バッチの加工処理レポートを返す。

    二段階処理（ステージ1: AI一次処理 / ステージ2: Python加工処理）の経緯を
    DataLineage（data_lineage コレクション）から取得し、構造化JSONで返す。
    Auditable AI（原則4）に基づき、各加工判定の根拠を含む。
    """
    db = firestore.client()
    batch_doc = db.collection("integration_batches").document(batch_id).get()
    if not batch_doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    batch = batch_doc.to_dict()
    status = batch.get("status")
    lineage_id = batch.get("lineage_id")

    # 処理が未完了、または lineage が未生成の場合は状態のみ返す
    if status != "done" or not lineage_id:
        return {
            "batch_id": batch_id,
            "status": status,
            "report": None,
            "detail": "レポートは処理完了後に利用できます" if status != "done" else "lineage が見つかりません",
            "error": batch.get("error"),
        }

    lineage_doc = db.collection("data_lineage").document(lineage_id).get()
    if not lineage_doc.exists:
        raise HTTPException(status_code=404, detail="Lineage not found")
    lineage = lineage_doc.to_dict()

    return {
        "batch_id": batch_id,
        "status": status,
        "report": {
            "source": {
                "filename": lineage.get("source_filename"),
                "source_type": lineage.get("source_type"),
                "batch_id": lineage.get("batch_id"),
                "created_at": lineage.get("created_at"),
            },
            # ステージ1: AI一次処理（パスA=column_mapping / パスB=raw_extraction）
            "stage1_ai": {
                "column_mapping": lineage.get("column_mapping"),
                "raw_extraction": lineage.get("raw_extraction"),
            },
            # ステージ2: Python加工処理の判定根拠
            "stage2_transformations": {
                "transformations": lineage.get("transformations", []),
                "skipped_records": lineage.get("skipped_records", []),
            },
            "summary": lineage.get("transformation_summary"),
            "created_entity_ids": lineage.get("created_entity_ids", {}),
        },
    }


@router.get("/batches/{batch_id}/contacts")
async def get_batch_contacts(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    """バッチで取り込まれたコンタクト一覧を返す。"""
    db = firestore.client()
    batch_doc = db.collection("integration_batches").document(batch_id).get()
    if not batch_doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    event_id = batch_doc.to_dict().get("event_id", "unknown")
    contacts_snap = db.collection(
        f"events/{event_id}/batches/{batch_id}/contacts"
    ).get()
    contacts = [s.to_dict() for s in contacts_snap]
    return {"contacts": contacts, "count": len(contacts)}
