"""
Data Integration Router — /api/integration

ファイルアップロード・取り込みプラン提案・バッチ処理のエンドポイントを提供する。
docs/INGESTION_MAPPING.md / ADR-015 に従い、「承認済み BatchPlan がそのまま実行される」
契約を提供する（プレビューと実行が別々に AI を呼ぶ構成の廃止）。

  POST /api/integration/plan         → AI が BatchPlan（変換仕様 + 既定イベント提案）を返す
  POST /api/integration/batches      → 承認済み BatchPlan 付きで取り込みを開始
  GET  /api/integration/batches      → （提供しない。閲覧は /api/data に一本化）
  GET  /api/integration/batches/{id} → バッチ状態 + 報告 Markdown（stale sweep 付き）
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from dependencies import get_space_context
from ingestion.normalize import _normalize_name
from ingestion.readers import extraction_caveat, is_supported
from metering import metered
from ontology import BatchPlan
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])

# ハートビートがこの秒数以上更新されない processing ジョブは実行途絶とみなす
STALE_AFTER_SECONDS = 600


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _load_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    """アップロードを検証して読み込む。空 / 未対応形式（旧 .doc 等）は 400。"""
    loaded: list[tuple[str, bytes]] = []
    for f in files:
        filename = f.filename or "upload"
        if not is_supported(filename):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"未対応のファイル形式です: {filename}"
                    "（対応形式: CSV / Excel / テキスト / Word / PDF / PowerPoint）"
                ),
            )
        content = await f.read()
        if content:
            loaded.append((filename, content))
    if not loaded:
        raise HTTPException(status_code=400, detail="有効なファイルがありません")
    return loaded


def _existing_event_names(space: SpaceContext) -> list[str]:
    out: list[str] = []
    try:
        for doc in space.col("events").get():
            name = (doc.to_dict() or {}).get("name", "")
            if name:
                out.append(name)
    except Exception:
        pass
    return out


# ── 取り込みプラン提案（Understand → Confirm の材料）──────────────────────────────


@router.post("/plan")
async def plan_ingestion(
    files: list[UploadFile] = File(...),
    hint: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """ファイルを受け取り、BatchPlan（変換仕様）を生成して返す（保存しない）。

    ユーザーはこのプランを確認・修正し、そのまま POST /batches の `plan` に渡す。
    default_event.is_existing は既存イベント照合で P1 が確定する（AI は設定しない）。
    """
    from agents.data_integration_agent import UnderstandError, understand_batch

    loaded = await _load_files(files)
    existing = _existing_event_names(space)
    try:
        with metered(space):
            plan = await understand_batch(
                loaded, (hint or "").strip() or None, existing, space=space
            )
    except UnderstandError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if plan.default_event is not None:
        existing_norm = {_normalize_name(n) for n in existing}
        plan.default_event.is_existing = _normalize_name(plan.default_event.name) in existing_norm

    for fp in plan.files:
        fp.extraction_caveat = extraction_caveat(fp.filename)

    return plan.model_dump()


# ── バッチ処理（承認済み BatchPlan の実行）─────────────────────────────────────────


async def _run_integration(
    space: SpaceContext,
    batch_id: str,
    files: list[tuple[str, bytes]],
    hint: str | None,
    plan: BatchPlan | None,
) -> None:
    from agents.data_integration_agent import process_batch, understand_batch

    scoped = space.scoped_db()
    try:
        scoped.collection("integration_jobs").document(batch_id).update({"status": "processing"})
        with metered(space):
            if plan is None:
                # plan 省略時（API 直叩き）: 実行内で1回だけ Understand を実行して採用する。
                # UI 経由は常に承認済みプランを送る（承認と実行の一致）。
                plan = await understand_batch(
                    files, hint, _existing_event_names(space), space=space
                )
                scoped.collection("integration_jobs").document(batch_id).update(
                    {"plan": plan.model_dump()}
                )
            result = await process_batch(files, batch_id, scoped, plan, space=space)

        scoped.collection("integration_jobs").document(batch_id).update(
            {
                "status": "done",
                "created_entities": result.created_entities,
                "pending_count": result.pending_count,
                "skipped_count": result.skipped_count,
                "report_markdown": result.report_markdown,
            }
        )
        logger.info(
            "integration done: batch_id=%s created=%s pending=%d",
            batch_id,
            result.created_entities,
            result.pending_count,
        )
    except Exception as e:
        logger.exception("integration failed: batch_id=%s error=%s", batch_id, e)
        try:
            scoped.collection("integration_jobs").document(batch_id).update(
                {"status": "error", "error": str(e)[:500]}
            )
        except Exception:
            pass


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    plan: str | None = Form(None),
    hint: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """複数ファイルと承認済み BatchPlan でデータ統合バッチを開始する。

    plan は POST /plan が返した JSON（ユーザーが既定イベント等を修正したもの）。
    承認済みプランがそのまま実行され、実行側で理解をやり直さない（ADR-015 決定4）。
    plan 省略時は実行内で Understand を1回だけ実行する（API 直叩き向け）。
    """
    loaded = await _load_files(files)

    batch_plan: BatchPlan | None = None
    if plan:
        try:
            batch_plan = BatchPlan.model_validate(json.loads(plan))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"plan の形式が不正です: {e}") from e

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    filenames = [name for name, _ in loaded]

    space.col("integration_jobs").document(batch_id).set(
        {
            "job_id": batch_id,
            "batch_id": batch_id,
            "filenames": filenames,
            "hint": (hint or "").strip(),
            "plan": batch_plan.model_dump() if batch_plan else None,
            "status": "queued",
            "stage": "",
            "heartbeat_at": _now_iso(),
            "created_at": _now_iso(),
        }
    )

    background_tasks.add_task(
        _run_integration, space, batch_id, loaded, (hint or "").strip(), batch_plan
    )

    return {"batch_id": batch_id, "filenames": filenames}


def _is_stale(heartbeat_at: str) -> bool:
    if not heartbeat_at:
        return False
    try:
        beat = datetime.fromisoformat(heartbeat_at)
    except ValueError:
        return False
    return (datetime.now(UTC) - beat).total_seconds() > STALE_AFTER_SECONDS


@router.get("/batches/{batch_id}")
async def get_batch_status(
    batch_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """バッチの処理状況・生成エンティティ数・報告 Markdown を返す。

    processing のままハートビートが停止しているジョブは error に倒す（stale sweep）。
    """
    doc_ref = space.col("integration_jobs").document(batch_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Batch not found")
    data = doc.to_dict()

    if data.get("status") == "processing" and _is_stale(data.get("heartbeat_at", "")):
        try:
            doc_ref.update({"status": "error", "error": "実行途絶（ハートビート停止）"})
        except Exception:
            logger.exception("stale sweep update failed: batch_id=%s", batch_id)
        data["status"] = "error"
        data["error"] = "実行途絶（ハートビート停止）"

    return {
        "batch_id": batch_id,
        "status": data.get("status"),
        "stage": data.get("stage", ""),
        "filenames": data.get("filenames", []),
        "hint": data.get("hint", ""),
        "created_entities": data.get("created_entities", {}),
        "pending_count": data.get("pending_count", 0),
        "skipped_count": data.get("skipped_count", 0),
        "report_markdown": data.get("report_markdown", ""),
        "error": data.get("error"),
    }
