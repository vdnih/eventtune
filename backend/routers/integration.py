"""
Data Integration Router — /api/integration

ファイルアップロード・イベント提案・バッチ処理の3エンドポイントを提供する。

  POST /api/integration/suggest-event  → AI がファイル内容を読みイベント候補を返す
  POST /api/integration/batches        → file_event_map 付きで実際の取り込みを開始
  GET  /api/integration/batches/{id}   → バッチ状態の確認

バッチ一覧・加工レポート・コンタクト一覧は廃止。
データ閲覧は /api/data（汎用データブラウザ）で行う。
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
    proposed_events: list[_ProposedEvent] = []


class _SuggestResponse(BaseModel):
    suggestions: list[_FileSuggestion]


@router.post("/suggest-event")
async def suggest_event(
    files: list[UploadFile] = File(...),
    space: SpaceContext = Depends(get_space_context),
):
    """ファイルを受け取り、AIが既存イベントとの照合結果を返す（データは保存しない）。"""
    file_previews = []
    for f in files:
        content = await f.read()
        filename = f.filename or "upload"
        try:
            preview = content[:600].decode("utf-8", errors="replace")
        except Exception:
            preview = ""
        file_previews.append({"filename": filename, "preview": preview})

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
    - is_new_event が true（単一の新規イベント）→ proposed_events は **1件**。
    - is_multi_event が true（複数イベントに分割）→ proposed_events は **分割される件数ぶん（複数件）**。
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
    """file_event_map を {index: [event_id, ...]} 形式に正規化する。"""
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

        all_event_ids: list[str] = []
        for r in results:
            all_event_ids.extend(r.generated_event_ids)
        for ids in file_event_map.values():
            all_event_ids.extend(ids)
        event_ids = list(dict.fromkeys(all_event_ids))

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
