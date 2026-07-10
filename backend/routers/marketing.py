"""
Marketing Router — /api/marketing

MarketingAgent とのチャット（SSE）と、メール生成ランの管理を担う。
"""

import io
import json
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketing", tags=["marketing"])


# ── チャット (SSE) ──────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    thread_id: str
    event_id: str | None = None


@router.post("/chat")
async def chat(
    body: ChatRequest,
    space: SpaceContext = Depends(get_space_context),
):
    """
    MarketingAgent とのチャット。Server-Sent Events でストリーミングする。
    各イベントは JSON 文字列として `data: {...}\n\n` 形式で送信される。

    thread_id は表示スレッド（フロントが会話開始時に採番するクライアント UUID）。
    Agent Engine セッション（agent_session_id）とは別物なので、StreamingResponse を
    返す前に同期的に「スレッドの所有者チェック → セッション確定 → 対応づけの永続化」を
    済ませておく（ストリーム内の best-effort な永続化ブロックに埋もれさせない）。
    """
    import thread_store
    from agents.marketing_agent import chat_stream, ensure_session

    # thread_id はクライアント採番の UUID で信用境界にならないため、所有者チェックを
    # 必ず通す。他人のスレッドなら 404（存在しない場合と区別しない）。
    if not thread_store.touch_thread(space, body.thread_id, body.message):
        raise HTTPException(status_code=404, detail="Thread not found")

    # Agent Engine の session_id はサーバ採番。既存IDは resume、未指定なら新規採番する。
    existing_agent_session_id = thread_store.get_agent_session_id(space, body.thread_id)
    try:
        agent_session_id = await ensure_session(existing_agent_session_id, space)
    except Exception as e:
        logger.exception("ensure_session failed")
        raise HTTPException(status_code=502, detail="セッションの初期化に失敗しました") from e

    if agent_session_id != existing_agent_session_id:
        thread_store.set_agent_session_id(space, body.thread_id, agent_session_id)

    async def event_generator():
        # user メッセージを再表示用スナップショットとして保存する。
        # 永続化の失敗はチャット自体を止めない（ベストエフォート）。
        try:
            thread_store.append_message(
                space, body.thread_id, {"role": "user", "content": body.message}
            )
        except Exception:
            logger.exception("thread persist (user) failed: thread_id=%s", body.thread_id)

        # assistant メッセージのスナップショットをサーバ側でも組み立てる（フロントの解釈に合わせる）。
        acc_text = ""
        tool_calls: list[dict] = []
        code_blocks: list[dict] = []
        run_id: str | None = None
        try:
            async for event in chat_stream(
                message=body.message,
                session_id=agent_session_id,
                thread_id=body.thread_id,
                space=space,
                event_id=body.event_id,
            ):
                etype = event.get("type")
                if etype == "text":
                    acc_text += event.get("text", "")
                elif etype == "tool_call":
                    tool_calls.append(
                        {"tool_name": event.get("tool_name"), "args": event.get("args", {})}
                    )
                elif etype == "tool_result":
                    rid = thread_store.extract_run_id_from_result(event)
                    if rid:
                        run_id = rid
                elif etype == "code":
                    code_blocks.append({"code": event.get("code", "")})
                elif etype == "code_result":
                    # 直近の未完了コードブロックに実行結果を紐づける
                    for b in reversed(code_blocks):
                        if "output" not in b:
                            b["output"] = event.get("output", "")
                            b["outcome"] = event.get("outcome")
                            break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("chat SSE error")
            err_event = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"
        finally:
            if run_id is None:
                run_id = thread_store.extract_run_id_from_text(acc_text)
            try:
                thread_store.append_message(
                    space,
                    body.thread_id,
                    {
                        "role": "assistant",
                        "content": acc_text,
                        "tool_calls": tool_calls,
                        "code_blocks": code_blocks,
                        "run_id": run_id,
                    },
                )
            except Exception:
                logger.exception(
                    "thread persist (assistant) failed: thread_id=%s", body.thread_id
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Agent-Session-Id": agent_session_id,
        },
    )


# ── メール生成ラン ─────────────────────────────────────────────────────────
# 組み立ては MarketingAgent の run_assembly ツールが決定論的に行い、run は作成時点で
# 完了済み（status=done）。フロントは状態取得・結果取得・CSVエクスポートのみ行う。


@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """メール生成ランの進捗を返す。"""
    doc = space.col("marketing_runs").document(run_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Run not found")
    data = doc.to_dict()
    return {
        "run_id": run_id,
        "status": data.get("status"),
        "purpose": data.get("purpose"),
        "total": data.get("total", 0),
        "done": data.get("done", 0),
        "deliverable_count": data.get("deliverable_count", 0),
        "snapshot_id": data.get("snapshot_id"),
        "error": data.get("error"),
    }


@router.get("/runs/{run_id}/results")
async def get_run_results(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """生成された成果物（Deliverable）一覧を返す。"""
    deliverables = [s.to_dict() for s in space.col(f"marketing_runs/{run_id}/deliverables").get()]
    return {"deliverables": deliverables, "count": len(deliverables)}


@router.get("/runs/{run_id}/export")
async def export_run_results(
    run_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """生成された成果物を CSV でエクスポートする。"""
    deliverables = [s.to_dict() for s in space.col(f"marketing_runs/{run_id}/deliverables").get()]
    if not deliverables:
        raise HTTPException(status_code=404, detail="成果物がまだ生成されていません")

    rows = []
    for dlv in deliverables:
        blocks = dlv.get("blocks", [])
        full_text = "\n\n".join(b.get("block_text", "") for b in blocks)
        reasons = "\n".join(
            f"[{b.get('block_type', '')}] {b.get('reason_for_inclusion', '')}" for b in blocks
        )
        rows.append(
            {
                "person_id": dlv.get("person_id", ""),
                "bucket": dlv.get("bucket", ""),
                "件名": dlv.get("subject", ""),
                "本文（全体）": full_text,
                "包含根拠": reasons,
            }
        )

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename=deliverables_{run_id[:8]}.csv"},
    )
