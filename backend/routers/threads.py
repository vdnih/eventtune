"""
Threads Router — /api/marketing/threads

チャットスレッド（会話）の一覧・リネーム・削除・メッセージ取得を提供する薄い REST。
スレッドの作成と本文の永続化は marketing.py の chat エンドポイントが担う
（chat 時に thread_store.touch_thread / append_message を呼ぶ）。

スレッドはユーザーごと非公開。所有者(uid)以外には見えない/操作できない。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import thread_store
from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketing", tags=["threads"])


class RenameThreadRequest(BaseModel):
    title: str


@router.get("/threads")
async def list_threads(space: SpaceContext = Depends(get_space_context)):
    """自分のスレッドを updated_at 降順で返す。"""
    threads = thread_store.list_threads(space)
    return {"threads": threads, "count": len(threads)}


@router.get("/threads/{thread_id}/messages")
async def get_thread_messages(
    thread_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """スレッドのメッセージを seq 昇順で返す（再表示用）。"""
    msgs = thread_store.get_messages(space, thread_id)
    if msgs is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"messages": msgs, "count": len(msgs)}


@router.patch("/threads/{thread_id}")
async def rename_thread(
    thread_id: str,
    body: RenameThreadRequest,
    space: SpaceContext = Depends(get_space_context),
):
    """スレッドのタイトルを変更する。"""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    result = thread_store.rename_thread(space, thread_id, title[:80])
    if result is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return result


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """スレッドとメッセージを削除する。"""
    if not thread_store.delete_thread(space, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return None
