"""
Thread Store — チャットスレッドの永続化（再表示用 UI スナップショット）

「表示スレッド（thread_id）」と「Agent Engine セッション（agent_session_id）」は別物。
thread_id はフロントが会話開始時に採番するクライアント UUID で、marketing / ingestion
どちらのターンも同じ thread_id 配下にメッセージを書き込む（1つの会話として統合表示する
ための入れ物）。agent_session_id は Agent Engine（VertexAiSessionService）がサーバ側で
採番する ID で、marketing チャットの「論理的真実」（会話文脈）はそちらが担う。ingestion
は ADK を使わないため agent_session_id を持たない。ここはあくまで左ペインの一覧とリロー
ド時の再表示のための UI スナップショットを Firestore に保存する薄い決定論レイヤ。

thread_id はクライアント採番のため、それ自体を信用境界にしない。書き込み時は必ず
uid 一致を確認する（touch_thread の戻り値 False = 所有者不一致/他人のスレッド）。

データモデル:
  spaces/{space_id}/threads/{thread_id}                    (agent_session_id は nullable)
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


def touch_thread(space: SpaceContext, thread_id: str, first_message: str) -> bool:
    """スレッドを upsert する。新規なら uid/title/created_at をセット、毎回 updated_at を更新。

    thread_id はクライアント採番の UUID であり、それ自体を信用境界にしない
    （所有者チェックを構造で担保する）。既存スレッドが他ユーザー所有なら何もせず
    False を返す。呼び出し元はこれを 404 相当として扱うこと
    （rename_thread/delete_thread/get_messages と同じ「存在しない/所有者不一致」規約）。
    """
    ref = space.col("threads").document(thread_id)
    snap = ref.get()
    now = _now_iso()
    if snap.exists:
        if snap.to_dict().get("uid") != space.uid:
            return False
        ref.update({"updated_at": now})
        return True
    ref.set(
        {
            "thread_id": thread_id,
            "agent_session_id": None,
            "uid": space.uid,
            "title": _title_from_message(first_message),
            "title_custom": False,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
    )
    return True


def get_agent_session_id(space: SpaceContext, thread_id: str) -> str | None:
    """スレッドに紐づく Agent Engine の session_id を返す（未確定なら None）。

    旧データ（agent_session_id を持たず session_id フィールドのみ持つスレッド）との
    後方互換のため、agent_session_id が無ければ旧 session_id フィールドへフォールバックする。
    """
    snap = space.col("threads").document(thread_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    return data.get("agent_session_id") or data.get("session_id")


def set_agent_session_id(space: SpaceContext, thread_id: str, agent_session_id: str) -> None:
    """初回 marketing ターンで確定した Agent Engine session_id をスレッドに記録する。"""
    try:
        space.col("threads").document(thread_id).update({"agent_session_id": agent_session_id})
    except Exception:
        logger.exception(
            "set_agent_session_id failed: thread_id=%s agent_session_id=%s",
            thread_id,
            agent_session_id,
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


# ── ingestion → marketing 文脈連携 ────────────────────────────────────────────


def pop_unconsumed_ingestion_context(space: SpaceContext, thread_id: str) -> str | None:
    """同スレッドで完了済みだが未消費の取り込み結果を要約し、消費済みにマークして返す。

    marketing チャットの各ターン開始時に呼ぶ。「取り込み→そのまま分析」という頻出フローで、
    ADK セッションを持たない ingestion の結果を marketing 側の会話文脈へ橋渡しする決定論的な
    ロジック（AI には委ねない）。thread_id 単一条件のクエリ + Python 側フィルタで
    Firestore の複合インデックスを不要にしている。取得/更新に失敗しても呼び出し元の
    チャット自体は止めない（次のターンで再度拾える best-effort）。
    """
    try:
        docs = list(
            space.col("integration_jobs").where(filter=FieldFilter("thread_id", "==", thread_id)).get()
        )
    except Exception:
        logger.exception("pop_unconsumed_ingestion_context: query failed thread_id=%s", thread_id)
        return None

    pending = []
    for d in docs:
        data = d.to_dict() or {}
        if data.get("status") == "done" and not data.get("consumed_by_chat"):
            pending.append((d.reference, data))
    if not pending:
        return None

    lines = ["[コンテキスト] このスレッドで直近取り込まれたデータ:"]
    for ref, data in pending:
        filenames = "、".join(data.get("filenames", [])) or "（ファイル名不明）"
        summary = (data.get("report_markdown") or "").strip()[:500]
        lines.append(f"- {filenames}: {summary}")
        try:
            ref.update({"consumed_by_chat": True})
        except Exception:
            logger.exception(
                "pop_unconsumed_ingestion_context: consumed flag update failed batch_id=%s",
                data.get("batch_id"),
            )
    return "\n".join(lines)


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
