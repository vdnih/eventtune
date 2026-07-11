"""Data ルーター（/api/data）の統合テスト。

シード済みオントロジーデータをスペース越境なしで閲覧できることを検証する。
"""

import pytest

pytestmark = pytest.mark.integration


def _seed_persons(db, space_id: str) -> None:
    db.document(f"spaces/{space_id}/persons/p1").set(
        {"person_id": "p1", "name": "山田太郎", "email": "yamada@example.com"}
    )
    db.document(f"spaces/{space_id}/persons/p2").set(
        {"person_id": "p2", "name": "佐藤花子", "source_job_id": "job_abc"}
    )


def test_list_collections_returns_views(make_client, seeded_space):
    client = make_client(uid="uid_member")
    res = client.get("/api/data/collections", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 200
    keys = {c["key"] for c in res.json()["collections"]}
    assert {"persons", "events", "cost_items", "deliverables"} <= keys


def test_list_view_returns_seeded_rows(make_client, seeded_space, db):
    _seed_persons(db, seeded_space)
    client = make_client(uid="uid_member")
    res = client.get("/api/data/persons", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    assert {r["name"] for r in body["rows"]} == {"山田太郎", "佐藤花子"}


def test_view_is_scoped_to_space(make_client, seeded_space, db):
    """別スペースのデータは同じビューから見えない（Context-Bound Data Access）。"""
    db.document("spaces/space_other/persons/px").set({"name": "他人"})
    db.document(f"spaces/{seeded_space}/persons/p1").set({"name": "自分"})
    client = make_client(uid="uid_member")
    res = client.get("/api/data/persons", headers={"X-Space-Id": seeded_space})
    assert [r["name"] for r in res.json()["rows"]] == ["自分"]


def test_unknown_view_returns_404(make_client, seeded_space):
    client = make_client(uid="uid_member")
    res = client.get("/api/data/nonexistent", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 404
