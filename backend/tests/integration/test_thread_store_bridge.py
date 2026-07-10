"""thread_store の新規ロジック（所有者ガード・agent_session_id・ingestion 文脈連携）の検証。

表示スレッド（thread_id）と Agent Engine セッション（agent_session_id）の分離、および
ingestion → marketing への文脈橋渡し（pop_unconsumed_ingestion_context）は
ルーター経由の統合テストではエミュレータ越しの Firestore クエリ挙動まで確認しづらいため、
thread_store の関数を直接 Firestore エミュレータに対して叩いて検証する。
"""

import pytest

import thread_store
from space import SpaceContext

pytestmark = pytest.mark.integration


def _space(db, uid: str, space_id: str = "space_test01", role: str = "owner") -> SpaceContext:
    return SpaceContext(space_id=space_id, uid=uid, role=role, db=db)


def test_touch_thread_owner_guard(db, seeded_space):
    owner = _space(db, "uid_owner")
    member = _space(db, "uid_member")

    assert thread_store.touch_thread(owner, "thread_a", "こんにちは") is True
    # 他ユーザーの thread_id を渡しても書き込めない（IDOR 対策）
    assert thread_store.touch_thread(member, "thread_a", "乗っ取り") is False

    doc = db.document(f"spaces/{seeded_space}/threads/thread_a").get().to_dict()
    assert doc["uid"] == "uid_owner"
    assert doc["title"] == "こんにちは"
    assert doc["agent_session_id"] is None


def test_agent_session_id_round_trip(db, seeded_space):
    space = _space(db, "uid_owner")
    thread_store.touch_thread(space, "thread_b", "分析して")

    assert thread_store.get_agent_session_id(space, "thread_b") is None
    thread_store.set_agent_session_id(space, "thread_b", "sess_abc")
    assert thread_store.get_agent_session_id(space, "thread_b") == "sess_abc"


def test_get_agent_session_id_falls_back_to_legacy_session_id_field(db, seeded_space):
    """旧データ（agent_session_id を持たず session_id のみ持つスレッド）との後方互換。"""
    space = _space(db, "uid_owner")
    db.document(f"spaces/{seeded_space}/threads/thread_legacy").set(
        {"thread_id": "thread_legacy", "uid": "uid_owner", "session_id": "sess_legacy"}
    )
    assert thread_store.get_agent_session_id(space, "thread_legacy") == "sess_legacy"


def test_pop_unconsumed_ingestion_context_summarizes_and_marks_consumed(db, seeded_space):
    space = _space(db, "uid_owner")
    thread_store.touch_thread(space, "thread_c", "展示会のデータを取り込んで")

    db.document(f"spaces/{seeded_space}/integration_jobs/batch_1").set(
        {
            "batch_id": "batch_1",
            "thread_id": "thread_c",
            "status": "done",
            "consumed_by_chat": False,
            "filenames": ["attendees.csv"],
            "report_markdown": "# 取り込み結果\n参加者20名を登録",
        }
    )

    context = thread_store.pop_unconsumed_ingestion_context(space, "thread_c")
    assert context is not None
    assert "attendees.csv" in context
    assert "参加者20名を登録" in context

    # 消費済みにマークされ、以後は None を返す（同じ内容を重複注入しない）
    job = db.document(f"spaces/{seeded_space}/integration_jobs/batch_1").get().to_dict()
    assert job["consumed_by_chat"] is True
    assert thread_store.pop_unconsumed_ingestion_context(space, "thread_c") is None


def test_pop_unconsumed_ingestion_context_ignores_other_threads_and_pending_jobs(db, seeded_space):
    space = _space(db, "uid_owner")
    thread_store.touch_thread(space, "thread_d", "テスト")

    # 別スレッドのジョブ
    db.document(f"spaces/{seeded_space}/integration_jobs/batch_other").set(
        {
            "batch_id": "batch_other",
            "thread_id": "thread_other",
            "status": "done",
            "consumed_by_chat": False,
            "filenames": ["x.csv"],
            "report_markdown": "他スレッドの結果",
        }
    )
    # 同スレッドだがまだ処理中のジョブ
    db.document(f"spaces/{seeded_space}/integration_jobs/batch_pending").set(
        {
            "batch_id": "batch_pending",
            "thread_id": "thread_d",
            "status": "processing",
            "consumed_by_chat": False,
        }
    )

    assert thread_store.pop_unconsumed_ingestion_context(space, "thread_d") is None
