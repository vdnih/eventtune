import io
import uuid

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from dependencies import get_current_user
from jobs import JobState, jobs

router = APIRouter(tags=["generate"])


async def _run_generate(job_id: str, df: pd.DataFrame) -> None:
    from agents.message_generator import generate_messages

    try:
        await generate_messages(job_id, df)
    except Exception as e:
        jobs[job_id].status = "error"
        jobs[job_id].error = str(e)


@router.post("/api/generate", status_code=202)
async def start_generate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    content = await file.read()
    df: pd.DataFrame | None = None

    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            df = pd.read_csv(io.BytesIO(content), encoding=encoding)
            break
        except Exception:
            continue

    if df is None:
        raise HTTPException(status_code=400, detail="CSVの読み込みに失敗しました")
    if len(df) == 0:
        raise HTTPException(status_code=400, detail="CSVにデータがありません")

    job_id = str(uuid.uuid4())
    jobs[job_id] = JobState(status="processing", total=len(df))

    background_tasks.add_task(_run_generate, job_id, df)

    return {"job_id": job_id, "total": len(df)}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response: dict = {
        "status": job.status,
        "done": job.done,
        "total": job.total,
        "error": job.error,
        "preview": None,
    }

    if job.status == "completed" and job.result_df is not None:
        df = job.result_df
        new_cols = ["案内コンテンツ種別", "案内コンテンツ名", "案内コンテンツURL", "個別メッセージ"]

        name_candidates = [c for c in df.columns if any(k in c for k in ("氏名", "名前", "担当者", "Name", "name"))]
        company_candidates = [c for c in df.columns if any(k in c for k in ("会社", "社名", "Company", "company"))]

        display_cols = []
        if name_candidates:
            display_cols.append(name_candidates[0])
        if company_candidates:
            display_cols.append(company_candidates[0])
        display_cols.extend(new_cols)

        preview_df = df[display_cols].head(5) if display_cols else df[new_cols].head(5)
        response["preview"] = preview_df.fillna("").to_dict("records")

    return response


@router.get("/api/jobs/{job_id}/download")
async def download_result(job_id: str, user: dict = Depends(get_current_user)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or job.result_df is None:
        raise HTTPException(status_code=400, detail="Result not ready")

    buf = io.BytesIO()
    job.result_df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=output.csv"},
    )
