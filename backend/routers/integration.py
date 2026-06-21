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
from google import genai
from google.genai import types
from pydantic import BaseModel

from dependencies import get_space_context
from metering import metered, record_llm_response
from space import SpaceContext

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

class _ProposedEvent(BaseModel):
    name: str = ""
    event_date: str | None = None
    event_type: str | None = None


class _FileSuggestion(BaseModel):
    filename: str
    event_id: str | None = None
    event_name: str | None = None
    event_date: str | None = None
    confidence: float = 0.0
    is_new_event: bool = False
    is_multi_event: bool = False
    # 新規（1件）/複数（N件）の場合に提示する仮タイトル候補。既存一致時は空。
    proposed_events: list[_ProposedEvent] = []


class _SuggestResponse(BaseModel):
    suggestions: list[_FileSuggestion]


@router.post("/suggest-event")
async def suggest_event(
    files: list[UploadFile] = File(...),
    space: SpaceContext = Depends(get_space_context),
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
    events_snap = space.col("events").get()
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
- proposed_events: 新規作成すべきイベントの仮タイトル候補リスト。各要素は {{name, event_date, event_type}}。
    - is_new_event が true（単一の新規イベント）→ proposed_events は **1件**。ファイル内容から推定した自然で具体的なイベント名を name に入れる。
    - is_multi_event が true（複数イベントに分割）→ proposed_events は **分割される件数ぶん（複数件）**。各イベントの名前・日付・種別を入れる。
    - 既存イベントに一致する場合 → proposed_events は **空リスト**。
  name は具体的に（例「2025春 ○○展示会」）。event_date は YYYY-MM-DD、不明なら null。
  event_type は「展示会」「セミナー」「プライベートイベント」のいずれか、不明なら null。

既存イベントが1件もない場合はすべて is_new_event: true にし、proposed_events に推定名を1件入れてください。
"""

    with metered(space):
        response = await _get_genai_client().aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SuggestResponse,
            ),
        )
    record_llm_response(space, _MODEL, response)
    result = _SuggestResponse.model_validate_json(response.text)
    return {"suggestions": [s.model_dump() for s in result.suggestions]}


# ── バッチ処理 ─────────────────────────────────────────────────────────────────

def _normalize_event_map(raw: dict) -> dict[str, list[str]]:
    """file_event_map を {filename: [event_id, ...]} 形式に正規化する。

    後方互換: 値が str なら [str]、null/空なら [] （AI が生成）、list なら空要素を除去。
    """
    normalized: dict[str, list[str]] = {}
    for filename, value in raw.items():
        if value is None or value == "":
            normalized[filename] = []
        elif isinstance(value, str):
            normalized[filename] = [value]
        elif isinstance(value, list):
            normalized[filename] = [v for v in value if v]
        else:
            normalized[filename] = []
    return normalized


async def _run_integration(
    space: SpaceContext,
    batch_id: str,
    files: list[tuple[str, bytes]],
    file_event_map: dict[str, list[str]],
) -> None:
    from agents.data_integration_agent import process_batch

    scoped = space.scoped_db()
    try:
        scoped.collection("integration_batches").document(batch_id).update({"status": "processing"})
        # コンピュート実行時間を計測（LLMトークンは process_batch 内で記録）
        with metered(space):
            results = await process_batch(files, batch_id, scoped, file_event_map, space=space)

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
        for ids in file_event_map.values():
            all_event_ids.extend(ids)
        event_ids = list(dict.fromkeys(all_event_ids))  # 順序保持で重複除去

        scoped.collection("integration_batches").document(batch_id).update({
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
            scoped.collection("integration_batches").document(batch_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


@router.get("/batches")
async def list_batches(
    event_id: str | None = None,
    space: SpaceContext = Depends(get_space_context),
):
    """データ統合バッチの一覧を返す。event_id が指定された場合は絞り込む。"""
    if event_id:
        docs = space.col("integration_batches").where("event_ids", "array_contains", event_id).get()
    else:
        docs = space.col("integration_batches").get()
    batches = [d.to_dict() for d in docs]
    batches.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"batches": batches, "count": len(batches)}


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    file_event_map: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """複数ファイルをアップロードしデータ統合バッチを開始する。

    file_event_map は JSON文字列で {"<index>": event_id | [event_id, ...] | null, ...} 形式。
    キーはアップロード順のインデックス（同名ファイルでも衝突しない）。
    値が空/null のファイルはコンテンツから AI がイベントを生成/解決する。
    複数 event_id を渡すと、そのファイルのデータを複数の既存イベントへ振り分ける。
    """
    raw_map: dict = {}
    if file_event_map:
        try:
            raw_map = json.loads(file_event_map)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="file_event_map が不正な JSON です")
    norm_map = _normalize_event_map(raw_map)

    # 空ファイルをスキップしつつ、元インデックスの割り当てを詰め直したインデックスへ再マップする
    # （process_batch は loaded の並び順インデックスで参照するため整合させる）。
    loaded: list[tuple[str, bytes]] = []
    parsed_map: dict[str, list[str]] = {}
    for orig_idx, f in enumerate(files):
        content = await f.read()
        if not content:
            continue
        new_idx = len(loaded)
        loaded.append((f.filename or "upload", content))
        parsed_map[str(new_idx)] = norm_map.get(str(orig_idx), [])

    if not loaded:
        raise HTTPException(status_code=400, detail="有効なファイルがありません")

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    filenames = [name for name, _ in loaded]
    all_event_ids = list(dict.fromkeys(eid for ids in parsed_map.values() for eid in ids))

    space.col("integration_batches").document(batch_id).set({
        "batch_id": batch_id,
        "filenames": filenames,
        "files": [{"filename": name, "status": "queued"} for name in filenames],
        "file_event_map": parsed_map,
        "event_ids": all_event_ids,
        "event_id": all_event_ids[0] if all_event_ids else None,
        "status": "queued",
        "created_at": _now_iso(),
    })

    background_tasks.add_task(_run_integration, space, batch_id, loaded, parsed_map)

    return {"batch_id": batch_id, "filenames": filenames}


@router.get("/batches/{batch_id}")
async def get_batch_status(
    batch_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """バッチの処理状況と生成されたエンティティ数を返す。"""
    doc = space.col("integration_batches").document(batch_id).get()
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
    space: SpaceContext = Depends(get_space_context),
):
    """バッチの加工処理レポートを返す（Auditable AI）。"""
    batch_doc = space.col("integration_batches").document(batch_id).get()
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
        lineage_doc = space.col("data_lineage").document(lid).get()
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
    space: SpaceContext = Depends(get_space_context),
):
    """バッチで取り込まれたコンタクト一覧を返す（複数イベントにまたがる場合も対応）。"""
    batch_doc = space.col("integration_batches").document(batch_id).get()
    if not batch_doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")

    data = batch_doc.to_dict()
    event_ids = data.get("event_ids") or ([data["event_id"]] if data.get("event_id") else ["unknown"])

    contacts: list[dict] = []
    for eid in event_ids:
        snap = space.col(f"events/{eid}/batches/{batch_id}/contacts").get()
        contacts.extend(s.to_dict() for s in snap)

    return {"contacts": contacts, "count": len(contacts)}
