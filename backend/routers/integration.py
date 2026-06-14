"""
Data Integration Router — /api/integration

ファイルアップロード・イベント提案・バッチ処理の3エンドポイントを提供する。

  POST /api/integration/suggest-event  → AI がファイル内容を読みイベント候補を返す
  POST /api/integration/batches        → file_event_map 付きで実際の取り込みを開始
  GET  /api/integration/batches        → バッチ一覧
  GET  /api/integration/batches/{id}   → バッチ状態
  GET  /api/integration/batches/{id}/report   → 加工レポート
  GET  /api/integration/batches/{id}/contacts → 取り込み済みコンタクト
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from firebase_admin import firestore
from google import genai
from google.genai import types
from pydantic import BaseModel

from dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])

_MODEL = "gemini-3.1-flash-lite"
_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── suggest-event ─────────────────────────────────────────────────────────────

class _FileSuggestion(BaseModel):
    filename: str
    event_id: str | None = None
    event_name: str | None = None
    event_date: str | None = None
    confidence: float = 0.0
    is_new_event: bool = False
    is_multi_event: bool = False


class _SuggestResponse(BaseModel):
    suggestions: list[_FileSuggestion]


@router.post("/suggest-event")
async def suggest_event(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """ファイルを受け取り、AIが既存イベントとの照合結果を返す（データは保存しない）。"""
    # ファイルプレビューを構築（テキスト系は先頭500文字、バイナリはファイル名のみ）
    file_previews = []
    for f in files:
        content = await f.read()
        filename = f.filename or "upload"
        try:
            preview = content[:600].decode("utf-8", errors="replace")
        except Exception:
            preview = ""
        file_previews.append({"filename": filename, "preview": preview})

    # 既存イベント一覧を取得
    db = firestore.client()
    events_snap = db.collection("events").get()
    events_list = []
    for doc in events_snap:
        d = doc.to_dict()
        events_list.append({
            "event_id": d.get("event_id", doc.id),
            "name": d.get("name", ""),
            "event_date": d.get("event_date", ""),
            "event_type": d.get("event_type", ""),
        })
    events_list.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    prompt = f"""\
あなたはイベントマーケティングデータの専門家です。
アップロードされたファイルのリスト（ファイル名と先頭コンテンツ）を読み、
既存イベント一覧と照合して各ファイルが属するイベントを判定してください。

【既存イベント一覧】
{json.dumps(events_list, ensure_ascii=False)}

【アップロードファイル（ファイル名と先頭コンテンツ）】
{json.dumps(file_previews, ensure_ascii=False)}

各ファイルについて以下を判定してください:
- event_id: 最も適切な既存イベントの event_id（見つからない場合は null）
- event_name: 対応するイベント名（null の場合は null）
- event_date: 対応するイベント開催日（null の場合は null）
- confidence: 一致の確信度 0.0〜1.0
- is_new_event: true = 新しいイベントとして取り込むべき（既存イベントに対応しない）
- is_multi_event: true = このファイルには複数のイベントのデータが含まれる可能性がある

既存イベントが1件もない場合はすべて is_new_event: true にしてください。
"""

    response = await _get_genai_client().aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SuggestResponse,
        ),
    )
    result = _SuggestResponse.model_validate_json(response.text)
    return {"suggestions": [s.model_dump() for s in result.suggestions]}


# ── バッチ処理 ─────────────────────────────────────────────────────────────────

async def _run_integration(
    batch_id: str,
    files: list[tuple[str, bytes]],
    file_event_map: dict[str, str | None],
) -> None:
    from agents.data_integration_agent import process_batch

    db = firestore.client()
    try:
        db.collection("integration_batches").document(batch_id).update({"status": "processing"})
        results = await process_batch(files, batch_id, db, file_event_map)

        merged: dict[str, int] = {}
        for r in results:
            for k, v in r.created_entities.items():
                merged[k] = merged.get(k, 0) + v

        any_ok = any(r.status == "done" for r in results)
        any_err = any(r.status == "error" for r in results)
        batch_status = "done" if any_ok else "error"
        lineage_ids = [r.lineage_id for r in results if r.lineage_id]

        # 全ファイルから生成・指定された event_id を収集
        all_event_ids: list[str] = []
        for r in results:
            all_event_ids.extend(r.generated_event_ids)
        all_event_ids.extend(v for v in file_event_map.values() if v)
        event_ids = list(dict.fromkeys(all_event_ids))  # 順序保持で重複除去

        db.collection("integration_batches").document(batch_id).update({
            "status": batch_status,
            "files": [r.to_dict() for r in results],
            "created_entities": merged,
            "event_ids": event_ids,
            "event_id": event_ids[0] if event_ids else None,
            "lineage_ids": lineage_ids,
            "lineage_id": lineage_ids[0] if lineage_ids else None,
            "partial": any_ok and any_err,
        })
        logger.info(
            "integration done: batch_id=%s status=%s created=%s event_ids=%s",
            batch_id, batch_status, merged, event_ids,
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
    """データ統合バッチの一覧を返す。event_id が指定された場合は絞り込む。"""
    db = firestore.client()
    if event_id:
        docs = db.collection("integration_batches").where("event_ids", "array_contains", event_id).get()
    else:
        docs = db.collection("integration_batches").get()
    batches = [d.to_dict() for d in docs]
    batches.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"batches": batches, "count": len(batches)}


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    file_event_map: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """複数ファイルをアップロードしデータ統合バッチを開始する。

    file_event_map は JSON文字列で {"filename": "event_id_or_null", ...} 形式。
    null 値のファイルはコンテンツから AI がイベントを生成/解決する。
    """
    loaded: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        if not content:
            continue
        loaded.append((f.filename or "upload", content))

    if not loaded:
        raise HTTPException(status_code=400, detail="有効なファイルがありません")

    parsed_map: dict[str, str | None] = {}
    if file_event_map:
        try:
            parsed_map = json.loads(file_event_map)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="file_event_map が不正な JSON です")

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    filenames = [name for name, _ in loaded]

    firestore.client().collection("integration_batches").document(batch_id).set({
        "batch_id": batch_id,
        "filenames": filenames,
        "files": [{"filename": name, "status": "queued"} for name in filenames],
        "file_event_map": parsed_map,
        "event_ids": [v for v in parsed_map.values() if v],
        "event_id": next((v for v in parsed_map.values() if v), None),
        "status": "queued",
        "created_at": _now_iso(),
    })

    background_tasks.add_task(_run_integration, batch_id, loaded, parsed_map)

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
        "filenames": data.get("filenames", []),
        "files": data.get("files", []),
        "event_id": data.get("event_id"),
        "event_ids": data.get("event_ids", []),
        "created_entities": data.get("created_entities", {}),
        "partial": data.get("partial", False),
        "error": data.get("error"),
    }


@router.get("/batches/{batch_id}/report")
async def get_batch_report(
    batch_id: str,
    user: dict = Depends(get_current_user),
):
    """バッチの加工処理レポートを返す（Auditable AI）。"""
    db = firestore.client()
    batch_doc = db.collection("integration_batches").document(batch_id).get()
    if not batch_doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    batch = batch_doc.to_dict()
    status = batch.get("status")
    lineage_ids = batch.get("lineage_ids") or ([batch["lineage_id"]] if batch.get("lineage_id") else [])

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
        "cross_file_summary": {
            "event_ids": batch.get("event_ids", []),
            "files": batch.get("files", []),
            "partial": batch.get("partial", False),
        },
        "reports": reports,
        "report": reports[0] if reports else None,
    }


def _format_lineage_report(lineage: dict) -> dict:
    return {
        "source": {
            "filename": lineage.get("source_filename"),
            "source_type": lineage.get("source_type"),
            "batch_id": lineage.get("batch_id"),
            "created_at": lineage.get("created_at"),
        },
        "stage1_ai": {
            "column_mapping": lineage.get("column_mapping"),
            "raw_extraction": lineage.get("raw_extraction"),
        },
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
    """バッチで取り込まれたコンタクト一覧を返す（複数イベントにまたがる場合も対応）。"""
    db = firestore.client()
    batch_doc = db.collection("integration_batches").document(batch_id).get()
    if not batch_doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    data = batch_doc.to_dict()
    event_ids = data.get("event_ids") or ([data["event_id"]] if data.get("event_id") else ["unknown"])

    contacts: list[dict] = []
    for eid in event_ids:
        snap = db.collection(f"events/{eid}/batches/{batch_id}/contacts").get()
        contacts.extend(s.to_dict() for s in snap)

    return {"contacts": contacts, "count": len(contacts)}
