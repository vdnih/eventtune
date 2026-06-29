"""
Data Integration Router — /api/integration

ファイルアップロード・取り込みプラン提案・バッチ処理のエンドポイントを提供する。
docs/INGESTION_MAPPING.md に従い、イベント割り当てではなく「ファイル→オントロジー分解」を行う。

  POST /api/integration/plan           → AI がファイル内容を読み分解プラン（種別＋リンク案）を返す
  POST /api/integration/batches        → hint 付きで実際の取り込みを開始
  GET  /api/integration/batches        → バッチ一覧
  GET  /api/integration/batches/{id}   → バッチ状態
  GET  /api/integration/batches/{id}/report   → 加工レポート
  GET  /api/integration/batches/{id}/contacts → 取り込み済み Person
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


# ── 取り込みプラン提案 ──────────────────────────────────────────────────────────

class _ProposedLink(BaseModel):
    kind: str = ""        # "event" | "account" | "product"
    name: str = ""
    existing: bool = False  # 既存マスタに一致するか


class _FilePlan(BaseModel):
    filename: str
    detected_entity_types: list[str] = []  # persons/accounts/events/products/contents/cost_items
    proposed_links: list[_ProposedLink] = []
    notes: str = ""


class _PlanResponse(BaseModel):
    files: list[_FilePlan]


def _load_master_names(space: SpaceContext) -> dict[str, list[str]]:
    """既存マスタ名（events/accounts/products）を取得し、リンク照合のヒントにする。"""
    out: dict[str, list[str]] = {"events": [], "accounts": [], "products": []}
    for col, field in (("events", "name"), ("accounts", "account_name"), ("products", "product_name")):
        try:
            for doc in space.col(col).get():
                name = (doc.to_dict() or {}).get(field, "")
                if name:
                    out[col].append(name)
        except Exception:
            pass
    return out


@router.post("/plan")
async def plan_ingestion(
    files: list[UploadFile] = File(...),
    hint: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """ファイルを受け取り、AI が分解プラン（エンティティ種別＋リンク案）を返す（保存しない）。"""
    file_previews = []
    for f in files:
        content = await f.read()
        filename = f.filename or "upload"
        try:
            preview = content[:800].decode("utf-8", errors="replace")
        except Exception:
            preview = ""
        file_previews.append({"filename": filename, "preview": preview})

    masters = _load_master_names(space)
    hint_block = f"\n\n【ユーザーのヒント】\n{hint.strip()}\n" if (hint or "").strip() else ""

    prompt = f"""\
あなたはイベントマーケティングデータの統合専門家です。
アップロードされたファイル（ファイル名と先頭コンテンツ）を読み、各ファイルがオントロジーの
どのエンティティを含むか、どのマスタにリンクするかを判定してください。イベントは複数マスタの
1つにすぎません（ファイルを単一イベントに割り当てる発想はしないこと）。

【オントロジーのエンティティ種別】
persons（人物）, accounts（企業）, events（イベント）, products（製品）, contents（素材）, cost_items（費用）
※1ファイルに複数種別が含まれることがある（例: 参加者リスト = persons ＋ accounts ＋ events へのリンク）

【既存マスタ名（リンク照合用）】
events: {json.dumps(masters["events"], ensure_ascii=False)}
accounts: {json.dumps(masters["accounts"], ensure_ascii=False)}
products: {json.dumps(masters["products"], ensure_ascii=False)}
{hint_block}
【アップロードファイル】
{json.dumps(file_previews, ensure_ascii=False)}

各ファイルについて判定:
- detected_entity_types: 含まれるエンティティ種別のリスト
- proposed_links: このファイルのデータが紐づくマスタ案。各要素 {{kind: event|account|product, name: 名称, existing: 既存マスタ名に一致するか}}。
  行ごとに異なる場合や列で判別できる場合も、代表的なリンク先を挙げる。リンクが無ければ空配列。
- notes: 判定の補足（曖昧な点、ヒントの反映など）を簡潔に
"""

    with metered(space):
        response = await _get_genai_client().aio.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PlanResponse,
            ),
        )
    record_llm_response(space, _MODEL, response)
    result = _PlanResponse.model_validate_json(response.text)
    return {"files": [f.model_dump() for f in result.files]}


# ── バッチ処理 ─────────────────────────────────────────────────────────────────

async def _run_integration(
    space: SpaceContext,
    batch_id: str,
    files: list[tuple[str, bytes]],
    hint: str | None,
) -> None:
    from agents.data_integration_agent import process_batch

    scoped = space.scoped_db()
    try:
        scoped.collection("integration_jobs").document(batch_id).update({"status": "processing"})
        with metered(space):
            results = await process_batch(files, batch_id, scoped, hint=hint, space=space)

        merged: dict[str, int] = {}
        for r in results:
            for k, v in r.created_entities.items():
                merged[k] = merged.get(k, 0) + v

        any_ok = any(r.status == "done" for r in results)
        any_err = any(r.status == "error" for r in results)
        batch_status = "done" if any_ok else "error"
        child_job_ids = [r.job_id for r in results if r.job_id]

        scoped.collection("integration_jobs").document(batch_id).update({
            "status": batch_status,
            "files": [r.to_dict() for r in results],
            "created_entities": merged,
            "child_job_ids": child_job_ids,
            "partial": any_ok and any_err,
        })
        logger.info(
            "integration done: batch_id=%s status=%s created=%s",
            batch_id, batch_status, merged,
        )

    except Exception as e:
        logger.exception("integration failed: batch_id=%s error=%s", batch_id, e)
        try:
            scoped.collection("integration_jobs").document(batch_id).update({
                "status": "error",
                "error": str(e)[:500],
            })
        except Exception:
            pass


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    hint: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """複数ファイルをアップロードしデータ統合バッチを開始する。

    hint はユーザーの自然言語ヒント（曖昧なリンク解決・スコープ指定の補正）。全ファイル共通。
    取り込みエージェントがファイル内容を読み、オントロジーへ分解・リンク解決する。
    """
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

    space.col("integration_jobs").document(batch_id).set({
        "batch_id": batch_id,
        "filenames": filenames,
        "files": [{"filename": name, "status": "queued"} for name in filenames],
        "hint": (hint or "").strip(),
        "status": "queued",
        "created_at": _now_iso(),
    })

    background_tasks.add_task(_run_integration, space, batch_id, loaded, (hint or "").strip())

    return {"batch_id": batch_id, "filenames": filenames}


@router.get("/batches/{batch_id}")
async def get_batch_status(
    batch_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """バッチの処理状況と生成されたエンティティ数を返す。"""
    doc = space.col("integration_jobs").document(batch_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")
    data = doc.to_dict()
    return {
        "batch_id": batch_id,
        "status": data.get("status"),
        "filenames": data.get("filenames", []),
        "files": data.get("files", []),
        "hint": data.get("hint", ""),
        "created_entities": data.get("created_entities", {}),
        "partial": data.get("partial", False),
        "error": data.get("error"),
    }


