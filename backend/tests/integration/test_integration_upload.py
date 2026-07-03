"""Integration ルーター（/api/integration）の統合テスト。

アップロード→バッチ作成→バックグラウンド処理→状態取得のフローを検証する。
AI パイプライン（process_batch）は LLM を呼ぶためフェイクに差し替え、
ルーターの責務（ジョブドキュメントのライフサイクル・結果マージ）に焦点を当てる。
パイプライン内部のロジックは tests/unit/test_ingestion_pipeline.py が担う。
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_process_batch(monkeypatch):
    """process_batch を決定的なフェイクへ差し替える。

    ルーターは関数内 import（from agents.data_integration_agent import process_batch）
    のため、モジュール属性の差し替えが呼び出し時に反映される。
    """
    import agents.data_integration_agent as agent
    from agents.data_integration_agent import BatchFileResult

    calls: list[dict] = []

    async def _fake(files, batch_id, db, hint=None, space=None, event=None):
        calls.append({"files": [f for f, _ in files], "hint": hint, "event": event})
        return [
            BatchFileResult(
                filename=name,
                status="done",
                job_id=f"job_{i}",
                created_entities={"persons": 2, "events": 1},
            )
            for i, (name, _) in enumerate(files)
        ]

    monkeypatch.setattr(agent, "process_batch", _fake)
    return calls


def test_upload_creates_batch_and_processes(make_client, seeded_space, db, fake_process_batch):
    client = make_client(uid="uid_member")
    res = client.post(
        "/api/integration/batches",
        headers={"X-Space-Id": seeded_space},
        files=[("files", ("attendees.csv", b"name,email\nA,a@example.com\n", "text/csv"))],
        data={"hint": "展示会Xの参加者リスト"},
    )
    assert res.status_code == 202
    batch_id = res.json()["batch_id"]
    assert res.json()["filenames"] == ["attendees.csv"]

    # TestClient は BackgroundTasks をレスポンス後に同期実行する
    assert fake_process_batch == [
        {"files": ["attendees.csv"], "hint": "展示会Xの参加者リスト", "event": ""}
    ]

    status = client.get(
        f"/api/integration/batches/{batch_id}", headers={"X-Space-Id": seeded_space}
    )
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["created_entities"] == {"persons": 2, "events": 1}

    # ジョブドキュメントはスペース配下に作成される
    job = db.document(f"spaces/{seeded_space}/integration_jobs/{batch_id}").get()
    assert job.exists


def test_upload_merges_created_entities_across_files(make_client, seeded_space, fake_process_batch):
    client = make_client(uid="uid_member")
    res = client.post(
        "/api/integration/batches",
        headers={"X-Space-Id": seeded_space},
        files=[
            ("files", ("a.csv", b"x\n1\n", "text/csv")),
            ("files", ("b.csv", b"y\n2\n", "text/csv")),
        ],
    )
    batch_id = res.json()["batch_id"]
    body = client.get(
        f"/api/integration/batches/{batch_id}", headers={"X-Space-Id": seeded_space}
    ).json()
    assert body["created_entities"] == {"persons": 4, "events": 2}


def test_empty_upload_returns_400(make_client, seeded_space, fake_process_batch):
    client = make_client(uid="uid_member")
    res = client.post(
        "/api/integration/batches",
        headers={"X-Space-Id": seeded_space},
        files=[("files", ("empty.csv", b"", "text/csv"))],
    )
    assert res.status_code == 400


def test_pipeline_failure_marks_batch_error(make_client, seeded_space, monkeypatch):
    """process_batch が例外を投げてもジョブは error 状態で着地する（握りつぶさない）。"""
    import agents.data_integration_agent as agent

    async def _boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(agent, "process_batch", _boom)
    client = make_client(uid="uid_member")
    res = client.post(
        "/api/integration/batches",
        headers={"X-Space-Id": seeded_space},
        files=[("files", ("a.csv", b"x\n1\n", "text/csv"))],
    )
    batch_id = res.json()["batch_id"]
    body = client.get(
        f"/api/integration/batches/{batch_id}", headers={"X-Space-Id": seeded_space}
    ).json()
    assert body["status"] == "error"
    assert "pipeline exploded" in body["error"]


def test_unknown_batch_returns_404(make_client, seeded_space):
    client = make_client(uid="uid_member")
    res = client.get("/api/integration/batches/batch_none", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 404
