---
paths:
  - "backend/**"
---

# Backend (Python / FastAPI)

## コマンド（`backend/` で実行）

```bash
uv sync                          # 依存インストール
uv run uvicorn main:app --reload --port 8000
uv run pytest                    # ユニットのみ。既定で -m 'not integration'（統合は自動 deselect）
uv run pytest -m integration     # 統合テスト。FIRESTORE_EMULATOR_HOST が無ければ skip（本番誤接続防止）
uv run ruff check .
uv run ruff format .
```

統合テストはリポジトリルートから `firebase emulators:exec --only firestore,auth --project demo-eventtune "cd backend && uv run pytest -m integration"` で実行する。

## 実装上の注意

- `ontology.py` が全型定義の SSoT（Pydantic）。エンティティ定義を他所に複製しない。
- 認証と認可を分離する: 認証（本人確認）は `get_current_user`、認可（membership 照合）は `get_space_context` 以降。クライアント提示の `X-Space-Id` / role は信頼しない（Space-ID Trust Boundary。`tests/integration/test_auth_boundary.py` が保証）。
- 新しいルーターを追加したら `tests/integration/conftest.py` の `make_client` / `seeded_space` フィクスチャでテストを1本足す。
- ruff は `E501` を無視している（プロンプト文字列の折り返しで LLM 入力が変わるのを避けるため。意図的）。`main.py` は `E402` を無視（grpc ログ抑制のための import 前処理）。
