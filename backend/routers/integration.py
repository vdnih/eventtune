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
    files: list[tuple[str, bytes]],
    event_id: str | None,
) -> None:
    from agents.data_integration_agent import process_batch

    db = firestore.client()
    try:
        db.collection("integration_batches").document(batch_id).update({"status": "processing"})
        results, resolved_event_id = await process_batch(files, event_id, batch_id, db)

        # 全ファイル分の生成エンティティ数を合算
        merged: dict[str, int] = {}
        for r in results:
            for k, v in r.created_entities.items():
                merged[k] = merged.get(k, 0) + v

        any_ok = any(r.status == "done" for r in results)
        any_err = any(r.status == "error" for r in results)
        batch_status = "done" if any_ok else "error"
        lineage_ids = [r.lineage_id for r in results if r.lineage_id]

        db.collection("integration_batches").document(batch_id).update({
            "status": batch_status,
            "files": [r.to_dict() for r in results],
            "created_entities": merged,
            "resolved_event_id": resolved_event_id,
            # contacts エンドポイント互換のため resolved を反映
            "event_id": resolved_event_id or event_id,
            "lineage_ids": lineage_ids,
            "lineage_id": lineage_ids[0] if lineage_ids else None,  # 後方互換（先頭）
            "partial": any_ok and any_err,
        })
        logger.info(
            "integration done: batch_id=%s status=%s created=%s resolved_event_id=%s",
            batch_id, batch_status, merged, resolved_event_id,
        )

    except Exception as e:
        logger.exception("integration failed: batch_id=%s error=%s", batch_id, e)
        try:
            db.collection("integration_batches").document(batch_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


@router.get("/batches")
async def list_batches(
    event_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    """データ統合バッチの一覧を返す。event_id が指定されている場合はフィルタリングする。"""
    db = firestore.client()
    query = db.collection("integration_batches")
    if event_id:
        docs = query.where("event_id", "==", event_id).get()
    else:
        docs = query.get()
    batches = [d.to_dict() for d in docs]
    # 作成日時（created_at）の降順でソート
    batches.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"batches": batches, "count": len(batches)}


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    event_id: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """複数ファイルをまとめてアップロードし、データ統合バッチを開始する。

    同一イベントに属するファイル群（leads.csv + overview.txt + survey.txt 等）を
    1バッチとして横断処理し、ドキュメント由来の event_id を表形式データに伝播させる。
    """
    # 空ファイルはスキップし、(filename, content) のリストへ正規化
    loaded: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        if not content:
            continue
        loaded.append((f.filename or "upload", content))

    if not loaded:
        raise HTTPException(status_code=400, detail="有効なファイルがありません")

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    filenames = [name for name, _ in loaded]

    firestore.client().collection("integration_batches").document(batch_id).set({
        "batch_id": batch_id,
        "filenames": filenames,
        "filename": filenames[0],  # 後方互換（単数=先頭）
        "files": [{"filename": name, "status": "queued"} for name in filenames],
        "event_id": event_id,
        "status": "queued",
        "created_at": _now_iso(),
    })

    background_tasks.add_task(_run_integration, batch_id, loaded, event_id)

    return {"batch_id": batch_id, "filenames": filenames}


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
        "filenames": data.get("filenames", []),
        "files": data.get("files", []),
        "event_id": data.get("event_id"),
        "resolved_event_id": data.get("resolved_event_id"),
        "created_entities": data.get("created_entities", {}),
        "partial": data.get("partial", False),
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
    # 後方互換: 単数 lineage_id を先頭に、複数 lineage_ids に対応
    lineage_ids = batch.get("lineage_ids") or ([batch["lineage_id"]] if batch.get("lineage_id") else [])

    # 処理が未完了、または lineage が未生成の場合は状態のみ返す
    if status != "done" or not lineage_ids:
        return {
            "batch_id": batch_id,
            "status": status,
            "report": None,
            "reports": [],
            "detail": "レポートは処理完了後に利用できます" if status != "done" else "lineage が見つかりません",
            "error": batch.get("error"),
        }

    reports = []
    for lid in lineage_ids:
        lineage_doc = db.collection("data_lineage").document(lid).get()
        if lineage_doc.exists:
            reports.append(_format_lineage_report(lineage_doc.to_dict()))

    return {
        "batch_id": batch_id,
        "status": status,
        # ファイル横断の伝播サマリ（Auditable AI: どのファイルからどの event_id が伝播したか）
        "cross_file_summary": {
            "resolved_event_id": batch.get("resolved_event_id"),
            "files": batch.get("files", []),
            "partial": batch.get("partial", False),
        },
        # ファイルごとの加工レポート
        "reports": reports,
        # 後方互換（単数=先頭ファイル）
        "report": reports[0] if reports else None,
    }


def _format_lineage_report(lineage: dict) -> dict:
    """DataLineage ドキュメントを構造化レポート形式に整形する。"""
    return {
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
