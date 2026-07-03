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

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from agents.ontology_mapper import _normalize_name
from dependencies import get_space_context
from metering import metered
from ontology import DocumentPlan
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── 取り込みプラン提案 ──────────────────────────────────────────────────────────


class _FilePlan(BaseModel):
    filename: str
    business_context: str = ""
    entity_type: str = ""
    source_file_role: str = ""
    link_hints: dict[str, str] = {}  # {kind: マスタ名}
    link_existing: dict[str, bool] = {}  # {kind: 既存マスタに一致するか}
    column_map: dict[str, str] = {}
    unmapped_notes: str = ""


class _PlanResponse(BaseModel):
    files: list[_FilePlan]


def _load_master_names(space: SpaceContext) -> dict[str, list[str]]:
    """既存マスタ名（events/accounts/products）を取得し、リンク照合のヒントにする。"""
    out: dict[str, list[str]] = {"events": [], "accounts": [], "products": []}
    for col, field in (
        ("events", "name"),
        ("accounts", "account_name"),
        ("products", "product_name"),
    ):
        try:
            for doc in space.col(col).get():
                name = (doc.to_dict() or {}).get(field, "")
                if name:
                    out[col].append(name)
        except Exception:
            pass
    return out


_KIND_TO_COL = {"event": "events", "account": "accounts", "product": "products"}


@router.post("/plan")
async def plan_ingestion(
    files: list[UploadFile] = File(...),
    hint: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """ファイルを受け取り、understand_batch で分解プランを生成して返す（保存しない）。"""
    from agents.data_integration_agent import understand_batch

    loaded: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        loaded.append((f.filename or "upload", content))

    with metered(space):
        document_plans = await understand_batch(loaded, (hint or "").strip() or None, space=space)

    masters = _load_master_names(space)

    result_files: list[_FilePlan] = []
    for filename, _ in loaded:
        plan: DocumentPlan = document_plans.get(filename, DocumentPlan())
        link_existing: dict[str, bool] = {}
        for kind, name in plan.link_hints.items():
            col = _KIND_TO_COL.get(kind, kind + "s")
            norm_name = _normalize_name(name)
            link_existing[kind] = norm_name in {_normalize_name(n) for n in masters.get(col, [])}
        result_files.append(
            _FilePlan(
                filename=filename,
                business_context=plan.business_context,
                entity_type=plan.entity_type,
                source_file_role=plan.source_file_role,
                link_hints=plan.link_hints,
                link_existing=link_existing,
                column_map=plan.column_map,
                unmapped_notes=plan.unmapped_notes,
            )
        )

    return {"files": [f.model_dump() for f in result_files]}


# ── バッチ処理 ─────────────────────────────────────────────────────────────────


async def _run_integration(
    space: SpaceContext,
    batch_id: str,
    files: list[tuple[str, bytes]],
    hint: str | None,
    event: str | None = None,
) -> None:
    from agents.data_integration_agent import process_batch

    scoped = space.scoped_db()
    try:
        scoped.collection("integration_jobs").document(batch_id).update({"status": "processing"})
        with metered(space):
            results = await process_batch(
                files, batch_id, scoped, hint=hint, space=space, event=event
            )

        merged: dict[str, int] = {}
        for r in results:
            for k, v in r.created_entities.items():
                merged[k] = merged.get(k, 0) + v

        any_ok = any(r.status == "done" for r in results)
        any_err = any(r.status == "error" for r in results)
        batch_status = "done" if any_ok else "error"
        child_job_ids = [r.job_id for r in results if r.job_id]

        scoped.collection("integration_jobs").document(batch_id).update(
            {
                "status": batch_status,
                "files": [r.to_dict() for r in results],
                "created_entities": merged,
                "child_job_ids": child_job_ids,
                "partial": any_ok and any_err,
            }
        )
        logger.info(
            "integration done: batch_id=%s status=%s created=%s",
            batch_id,
            batch_status,
            merged,
        )

    except Exception as e:
        logger.exception("integration failed: batch_id=%s error=%s", batch_id, e)
        try:
            scoped.collection("integration_jobs").document(batch_id).update(
                {
                    "status": "error",
                    "error": str(e)[:500],
                }
            )
        except Exception:
            pass


@router.post("/batches", status_code=202)
async def start_integration(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    hint: str | None = Form(None),
    event: str | None = Form(None),
    space: SpaceContext = Depends(get_space_context),
):
    """複数ファイルをアップロードしデータ統合バッチを開始する。

    hint はユーザーの自然言語ヒント（曖昧なリンク解決・スコープ指定の補正）。全ファイル共通。
    event は明示的な既定イベント名（行にイベントリンクが無いとき使う。hint より強いシグナル）。
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

    space.col("integration_jobs").document(batch_id).set(
        {
            "batch_id": batch_id,
            "filenames": filenames,
            "files": [{"filename": name, "status": "queued"} for name in filenames],
            "hint": (hint or "").strip(),
            "event": (event or "").strip(),
            "status": "queued",
            "created_at": _now_iso(),
        }
    )

    background_tasks.add_task(
        _run_integration, space, batch_id, loaded, (hint or "").strip(), (event or "").strip()
    )

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
