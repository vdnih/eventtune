"""Marketing チャット（SSE）の疎通スモーク。

ADK Runner / Agent Engine は CI で動かせないため chat_stream / ensure_session を
フェイクに差し替え、SSE の配管（ヘッダ・イベント整形・スレッド永続化）だけを検証する。
"""

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_agent(monkeypatch):
    import agents.marketing_agent as ma

    async def fake_ensure_session(session_id, space):
        return session_id or "sess_test"

    async def fake_chat_stream(message, session_id, thread_id, space, event_id=None):
        yield {"type": "text", "text": "こんにちは、"}
        yield {"type": "text", "text": "分析を始めます。"}

    monkeypatch.setattr(ma, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(ma, "chat_stream", fake_chat_stream)


def test_chat_streams_sse_events(make_client, seeded_space, db, fake_agent):
    client = make_client(uid="uid_member")
    with client.stream(
        "POST",
        "/api/marketing/chat",
        headers={"X-Space-Id": seeded_space},
        json={"message": "イベントの振り返りをして", "thread_id": "thread_test"},
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["x-agent-session-id"] == "sess_test"
        events = []
        for line in res.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))

    assert [e["text"] for e in events if e["type"] == "text"] == [
        "こんにちは、",
        "分析を始めます。",
    ]

    # スレッド永続化: user + assistant のスナップショットが thread_id 配下に残る
    # （thread_id はクライアント採番で、Agent Engine の session_id とは別物）
    msgs = list(db.collection(f"spaces/{seeded_space}/threads/thread_test/messages").stream())
    roles = sorted(m.to_dict().get("role", "") for m in msgs)
    assert roles == ["assistant", "user"]

    thread_doc = db.collection(f"spaces/{seeded_space}/threads").document("thread_test").get()
    assert thread_doc.to_dict()["agent_session_id"] == "sess_test"
