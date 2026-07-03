"""
Thread Store — チャットスレッドの永続化（再表示用 UI スナップショット）

会話の「論理的真実」は Agent Engine セッション（VertexAiSessionService）が担う。
ここは左ペインの一覧とリロード時の再表示のための UI スナップショットを Firestore に
保存する薄い決定論レイヤ。AI には委ねない明示的な業務ロジック。

「スレッド = session_id」の 1:1 対応（thread_id == Agent Engine の session_id）。
スレッドはユーザーごと非公開（uid で絞り込む）。

データモデル:
  spaces/{space_id}/threads/{thread_id}                    (thread_id == session_id)
  spaces/{space_id}/threads/{thread_id}/messages/{msg_NNNNNN}
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from google.cloud.firestore import FieldFilter

from space import SpaceContext

logger = logging.getLogger(__name__)

_TITLE_MAX = 40
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{12}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _title_from_message(message: str) -> str:
    """最初のユーザー発言からスレッドの簡単なタイトルを作る（改行・連続空白は畳む）。"""
    text = " ".join((message or "").split())
    return text[:_TITLE_MAX] if text else "新しいチャット"


# ── 永続化（チャット時に呼ばれる）─────────────────────────────────────────────


def touch_thread(space: SpaceContext, thread_id: str, first_message: str) -> None:
    """スレッドを upsert する。新規なら uid/title/created_at をセット、毎回 updated_at を更新。"""
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    now = _now_iso()
    if snap.exists:
        ref.update({"updated_at": now})
    else:
        ref.set(
            {
                "thread_id": thread_id,
                "session_id": thread_id,
                "uid": space.uid,
                "title": _title_from_message(first_message),
                "title_custom": False,
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
        )


def append_message(space: SpaceContext, thread_id: str, message: dict[str, Any]) -> None:
    """メッセージを seq 採番で追記し、thread.message_count/updated_at を更新する。"""
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    if not snap.exists:
        # touch_thread を経ずに呼ばれるのは想定外。安全側で何もしない。
        logger.warning("append_message: thread missing thread_id=%s", thread_id)
        return
    seq = int(snap.to_dict().get("message_count", 0))
    doc = {"seq": seq, "created_at": _now_iso(), **message}
    space.col(f"threads/{thread_id}/messages").document(f"msg_{seq:06d}").set(doc)
    ref.update({"message_count": seq + 1, "updated_at": _now_iso()})


# ── run_id 抽出（assistant メッセージのスナップショット用）─────────────────────


def extract_run_id_from_result(event: dict[str, Any]) -> str | None:
    """run_assembly ツールの tool_result から run_id を取り出す（フロントの解釈に合わせる）。"""
    if event.get("tool_name") != "run_assembly":
        return None
    result = event.get("result") or {}
    inner = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            return None
    if isinstance(inner, dict):
        rid = inner.get("run_id")
        return rid if isinstance(rid, str) else None
    return None


def extract_run_id_from_text(text: str) -> str | None:
    """応答テキストから run_id を拾う（tool_result で取れなかった場合のフォールバック）。"""
    m = _RUN_ID_RE.search(text or "")
    return m.group(0) if m else None


# ── 一覧・取得・リネーム・削除（threads ルーターから呼ばれる）──────────────────


def list_threads(space: SpaceContext) -> list[dict[str, Any]]:
    """自分（uid）のスレッドを updated_at 降順で返す。"""
    docs = space.col("threads").where(filter=FieldFilter("uid", "==", space.uid)).get()
    threads = [d.to_dict() for d in docs]
    threads.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return threads


def get_messages(space: SpaceContext, thread_id: str) -> list[dict[str, Any]] | None:
    """スレッドのメッセージを seq 昇順で返す。所有者でない/存在しない場合は None。"""
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("uid") != space.uid:
        return None
    msgs = [d.to_dict() for d in space.col(f"threads/{thread_id}/messages").get()]
    msgs.sort(key=lambda m: m.get("seq", 0))
    return msgs


def rename_thread(space: SpaceContext, thread_id: str, title: str) -> dict[str, Any] | None:
    """タイトルを変更する。所有者でない/存在しない場合は None。"""
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("uid") != space.uid:
        return None
    ref.update({"title": title, "title_custom": True, "updated_at": _now_iso()})
    return ref.get().to_dict()


def delete_thread(space: SpaceContext, thread_id: str) -> bool:
    """スレッドとメッセージを再帰削除する。所有者でない/存在しない場合は False。

    Agent Engine 側のマネージドセッションは残るが、履歴が残るだけで害はないため
    ここでは触らない（UI からは一覧・再表示できなくなる）。
    """
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("uid") != space.uid:
        return False
    space.db.recursive_delete(ref)
    return True
