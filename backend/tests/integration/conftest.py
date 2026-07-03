"""統合テスト共通フィクスチャ。

Firestore（+ Auth）エミュレータに対して FastAPI アプリを実際に叩く。
実行は firebase emulators:exec 経由を前提とする:

    firebase emulators:exec --only firestore,auth --project demo-eventtune \
        "cd backend && uv run pytest -m integration"

方針:
- 認証（本人確認）: get_current_user を dependency override で差し替え、任意の uid を注入する。
  Firebase IDトークンの署名検証は Google の責務でありテスト対象外。
- 認可（membership 照合）: get_space_context 以降は本物のコードパスを実エミュレータの
  Firestore に対して実行する（Space-ID Trust Boundary の検証がこのスイートの主目的）。
"""

import os

import pytest


def _require_emulator() -> str:
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
    if not host:
        pytest.skip(
            "FIRESTORE_EMULATOR_HOST が未設定です。"
            "firebase emulators:exec 経由で実行してください（docs/TESTING.md 参照）"
        )
    return host


@pytest.fixture(scope="session")
def app():
    """エミュレータ環境を確認したうえで FastAPI アプリを import する。

    main の import 時に firebase_admin.initialize_app が走るため、
    エミュレータ env（emulators:exec が設定）より後に import する必要がある。
    """
    _require_emulator()
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture(autouse=True)
def clear_firestore(app):
    """テスト毎に Firestore エミュレータの全ドキュメントを削除する。"""
    import httpx

    from config import get_settings

    host = os.environ["FIRESTORE_EMULATOR_HOST"]
    project = get_settings().firebase_project_id
    url = f"http://{host}/emulator/v1/projects/{project}/databases/(default)/documents"
    httpx.delete(url, timeout=10).raise_for_status()
    yield


@pytest.fixture
def make_client(app):
    """任意の uid で認証済みの TestClient を作る factory。

    uid=None なら override なし（実際の Bearer 検証パスを通る）。
    """
    from fastapi.testclient import TestClient

    from dependencies import get_current_user

    def _make(uid: str | None = None, email: str = "user@example.com") -> TestClient:
        app.dependency_overrides.pop(get_current_user, None)
        if uid is not None:
            app.dependency_overrides[get_current_user] = lambda: {"uid": uid, "email": email}
        # ヘッダは毎リクエスト明示付与する運用（X-Space-Id をテストで制御するため）
        return TestClient(app, headers={"Authorization": "Bearer test-token"})

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def db(app):
    """エミュレータに直接シード/検証するための Admin SDK クライアント。"""
    from firebase_admin import firestore

    return firestore.client()


@pytest.fixture
def seeded_space(db):
    """space + owner/member の membership をシードした状態を作る。"""
    space_id = "space_test01"
    db.document(f"spaces/{space_id}").set(
        {
            "space_id": space_id,
            "name": "テストスペース",
            "plan": "free",
            "owner_uid": "uid_owner",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    db.document(f"spaces/{space_id}/members/uid_owner").set(
        {
            "user_id": "uid_owner",
            "email": "owner@example.com",
            "role": "owner",
            "space_id": space_id,
            "space_name": "テストスペース",
        }
    )
    db.document(f"spaces/{space_id}/members/uid_member").set(
        {
            "user_id": "uid_member",
            "email": "member@example.com",
            "role": "member",
            "space_id": space_id,
            "space_name": "テストスペース",
        }
    )
    return space_id
