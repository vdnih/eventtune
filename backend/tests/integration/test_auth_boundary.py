"""Space-ID Trust Boundary（dependencies.py）の統合テスト。

「クライアントが送る X-Space-Id / role は信頼せず、検証済み uid × サーバ保持の
members ドキュメントから認可を再導出する」という中核不変条件を、実 Firestore
（エミュレータ）に対する membership 照合込みで検証する。
"""

import pytest

pytestmark = pytest.mark.integration


def test_no_token_is_rejected(make_client, seeded_space):
    """Authorization ヘッダなしはアプリに到達する前に拒否される。"""
    from fastapi.testclient import TestClient

    client = make_client(uid=None)
    bare = TestClient(client.app)  # ヘッダなし
    res = bare.get(f"/api/spaces/{seeded_space}", headers={"X-Space-Id": seeded_space})
    assert res.status_code in (401, 403)


def test_garbage_token_returns_401(make_client, seeded_space):
    """不正なトークンは verify_id_token 失敗パスで 401（override なしの実パス）。"""
    client = make_client(uid=None)  # override なし → 実際の Bearer 検証
    res = client.get(f"/api/spaces/{seeded_space}", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 401


def test_member_can_access_space(make_client, seeded_space):
    client = make_client(uid="uid_member")
    res = client.get(f"/api/spaces/{seeded_space}", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 200
    body = res.json()
    # role はクライアント申告ではなく members ドキュメント由来
    assert body["role"] == "member"


def test_non_member_is_rejected_even_with_valid_space_id(make_client, seeded_space):
    """他人のスペースIDをヘッダに入れても membership が無ければ 403。"""
    client = make_client(uid="uid_stranger")
    res = client.get(f"/api/spaces/{seeded_space}", headers={"X-Space-Id": seeded_space})
    assert res.status_code == 403


def test_missing_space_header_is_rejected(make_client, seeded_space):
    """X-Space-Id ヘッダ必須（422: FastAPI の必須ヘッダ検証）。"""
    client = make_client(uid="uid_member")
    res = client.get(f"/api/spaces/{seeded_space}")
    assert res.status_code == 422


def test_member_cannot_perform_owner_action(make_client, seeded_space):
    """owner 専用操作（スペース設定変更）は member ロールでは 403。"""
    client = make_client(uid="uid_member")
    res = client.patch(
        f"/api/spaces/{seeded_space}",
        headers={"X-Space-Id": seeded_space},
        json={"name": "乗っ取り"},
    )
    assert res.status_code == 403


def test_owner_can_perform_owner_action(make_client, seeded_space):
    client = make_client(uid="uid_owner")
    res = client.patch(
        f"/api/spaces/{seeded_space}",
        headers={"X-Space-Id": seeded_space},
        json={"name": "改名後スペース"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "改名後スペース"
