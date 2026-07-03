"""Spaces ルーター（/api/spaces）の統合テスト。

作成→membership 生成→一覧→更新→削除のライフサイクルと、招待フロー
（Auth エミュレータ上の実ユーザー解決）を実 Firestore に対して検証する。
"""

import os

import pytest

pytestmark = pytest.mark.integration


def test_create_space_registers_owner_membership(make_client, db):
    """スペース作成時、owner はボディではなく検証済み uid から決定される。"""
    client = make_client(uid="uid_creator", email="creator@example.com")
    res = client.post("/api/spaces", json={"name": "新スペース", "description": "説明"})
    assert res.status_code == 201
    body = res.json()
    space_id = body["space_id"]
    assert body["owner_uid"] == "uid_creator"
    assert body["plan"] == "free"

    # Firestore 側の実体を検証（space doc + owner membership が原子的に作成される）
    space_doc = db.document(f"spaces/{space_id}").get()
    member_doc = db.document(f"spaces/{space_id}/members/uid_creator").get()
    assert space_doc.exists
    assert member_doc.exists
    assert member_doc.to_dict()["role"] == "owner"
    assert member_doc.to_dict()["email"] == "creator@example.com"


def test_list_my_spaces_returns_only_memberships(make_client, seeded_space):
    """一覧は members コレクショングループの横断クエリ。他人のスペースは見えない。"""
    client = make_client(uid="uid_member")
    other = make_client(uid="uid_other")
    other.post("/api/spaces", json={"name": "他人のスペース"})

    client = make_client(uid="uid_member")
    res = client.get("/api/spaces")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["spaces"][0]["space_id"] == seeded_space
    assert body["spaces"][0]["role"] == "member"


def test_update_space_name_syncs_member_denormalization(make_client, seeded_space, db):
    """name 変更時、members の非正規化 space_name も同期される。"""
    client = make_client(uid="uid_owner")
    res = client.patch(
        f"/api/spaces/{seeded_space}",
        headers={"X-Space-Id": seeded_space},
        json={"name": "改名後"},
    )
    assert res.status_code == 200
    for uid in ("uid_owner", "uid_member"):
        m = db.document(f"spaces/{seeded_space}/members/{uid}").get().to_dict()
        assert m["space_name"] == "改名後"


def test_space_id_path_header_mismatch_is_rejected(make_client, seeded_space):
    client = make_client(uid="uid_owner")
    res = client.get("/api/spaces/space_other", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 400


def test_delete_space_removes_all_data(make_client, seeded_space, db):
    """削除（owner のみ）は配下の全ドキュメントを再帰削除する。"""
    db.document(f"spaces/{seeded_space}/persons/p1").set({"name": "山田"})

    client = make_client(uid="uid_owner")
    res = client.delete(f"/api/spaces/{seeded_space}", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 204
    assert not db.document(f"spaces/{seeded_space}").get().exists
    assert not db.document(f"spaces/{seeded_space}/persons/p1").get().exists


@pytest.mark.skipif(
    not os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"),
    reason="招待フローは Auth エミュレータが必要（emulators:exec --only firestore,auth）",
)
class TestInviteMember:
    def test_invite_existing_user(self, make_client, seeded_space, db):
        from firebase_admin import auth

        invited = auth.create_user(email="invitee@example.com")
        client = make_client(uid="uid_owner")
        res = client.post(
            f"/api/spaces/{seeded_space}/members",
            headers={"X-Space-Id": seeded_space},
            json={"email": "invitee@example.com", "role": "member"},
        )
        assert res.status_code == 201
        m = db.document(f"spaces/{seeded_space}/members/{invited.uid}").get()
        assert m.exists
        assert m.to_dict()["role"] == "member"

    def test_invite_unknown_email_returns_404(self, make_client, seeded_space):
        client = make_client(uid="uid_owner")
        res = client.post(
            f"/api/spaces/{seeded_space}/members",
            headers={"X-Space-Id": seeded_space},
            json={"email": "ghost@example.com"},
        )
        assert res.status_code == 404

    def test_member_cannot_invite(self, make_client, seeded_space):
        client = make_client(uid="uid_member")
        res = client.post(
            f"/api/spaces/{seeded_space}/members",
            headers={"X-Space-Id": seeded_space},
            json={"email": "someone@example.com"},
        )
        assert res.status_code == 403
