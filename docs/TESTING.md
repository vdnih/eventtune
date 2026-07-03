# TESTING — テスト戦略とリグレッションテスト運用

機能追加をスピーディーに進めるため、既存機能の破壊を CI で即検知することを目的とする。
テストピラミッドに従い、下層ほど多く・速く、上層ほど少なく・本物に近く。

```
        E2E スモーク (Playwright)          … 2 シナリオ / 主要導線の生存確認
      ─────────────────────────────
      統合テスト (pytest + エミュレータ)    … 認可境界・ルーター・Firestore 実挙動
    ─────────────────────────────────
    ユニットテスト (pytest / Vitest)        … 決定的ロジック・LLM 出力パース・UI 部品
```

## ローカルでの実行方法

```bash
# --- backend（backend/ で実行）---
uv run pytest                    # ユニットのみ（デフォルト。統合は deselect される）
uv run ruff check . && uv run ruff format --check .   # lint / format

# --- backend 統合テスト（リポジトリルートで実行。Java が必要）---
firebase emulators:exec --only firestore,auth --project demo-eventtune \
  "cd backend && uv run pytest -m integration"

# --- frontend（frontend/ で実行）---
npm run lint && npm run typecheck
npm run test                     # Vitest（watch は npm run test:watch）

# --- E2E スモーク（リポジトリルートで実行）---
firebase emulators:exec --only firestore,auth --project demo-eventtune \
  "cd frontend && npx playwright test"
```

- `demo-` プレフィックスのプロジェクトIDはエミュレータ専用。実プロジェクトへの誤接続を構造的に防ぐ。
- 統合テストは `FIRESTORE_EMULATOR_HOST` が無ければ skip する（本番に繋がる事故を防ぐ）。
- E2E は Playwright の `webServer` がバックエンド（uvicorn :8000）とフロントエンド（next dev :3000）を自動起動する。

## 各層の設計方針

### ユニットテスト（backend: `backend/tests/unit/`, frontend: 対象と同階層の `*.test.ts(x)`）

**境界**: ネットワーク・Firestore・LLM を跨がずに正しさを判定できるコードが対象。

- backend: オントロジー変換（`agents/ontology_mapper.py`）、名寄せ（`EntityResolver`）、
  取り込みパイプライン（FakeDB + LLM monkeypatch）、LLM 出力の正規化（`_normalize_buckets`）
- frontend: API クライアントのヘッダ契約（`lib/api.ts`）、表示整形（`components/ui/format.ts`）、
  認証フック（`hooks/useAuth.ts`）、UI 部品のレンダリング

### 統合テスト（`backend/tests/integration/`、マーカー `integration`）

FastAPI `TestClient` で実アプリを叩き、Firestore はエミュレータの実挙動で検証する。

**認証と認可の分割**が中核パターン:
- 認証（本人確認）= `get_current_user` を dependency override で差し替え uid を注入
  （IDトークンの署名検証は Google の責務でテスト対象外）
- 認可（membership 照合）= `get_space_context` 以降は本物のコードパスを実行

新しいルーターを追加したら、`tests/integration/conftest.py` の `make_client` / `seeded_space`
フィクスチャを使ってテストを1本足すだけで検知網に入る。

### E2E スモーク（`frontend/e2e/`）

主要導線の生存確認のみ（最小に保つ）: 未認証リダイレクト / ログイン→同意→スペース作成→データ閲覧。
**LLM を呼ぶフローは対象外**とし、Gemini のモックを不要にして構成を軽く保つ。
ログインはエミュレータ限定の匿名テストログインを使う（Google ポップアップは
apis.google.com 依存で CI・プロキシ環境で不安定なため）。

## AI エージェントのテスト方針

確率的なコア（LLM）と決定的なシェルを分離し、CI では決定的な部分だけを固定する:

| 層 | 対象 | CI |
|---|---|---|
| 契約テスト | LLM 呼び出し前（プロンプト/スキーマ構築）と後（パース・不正出力への防御） | ✅ |
| ツールテスト | エージェントのツール関数をフェイク LLM 応答で直接実行 | ✅ |
| オーケストレーション | conform→bind→derive パイプラインがオントロジーへ正しく書くか | ✅ |
| 出力品質の評価 | 「良いメールが生成されるか」等 | ❌ 対象外 |

- **DataIntegrationAgent**: パイプライン性が強く、統合・ユニットテストの主戦場。
  LLM は `monkeypatch`（既存パターン: `test_ingestion_pipeline.py`）で差し替える。
- **MarketingAgent**: ADK Runner / Vertex AI Agent Engine は CI で動かさない。
  ツール関数・出力正規化・SSE 配管のみテストする。
- **出力品質の評価**（将来）: ゴールデンデータセット + ルーブリック評価を、PR ブロッキング
  ではない別枠（手動 or 定期実行）として導入する余地がある。非決定的・低速・課金ありのため
  CI リグレッションには入れない。

## テストが保証する不変条件（specification anchors）

1. **Space-ID Trust Boundary**: クライアント提示の `X-Space-Id` / role は信頼されない。
   認可は常に検証済み uid × members ドキュメントから再導出される
   （`tests/integration/test_auth_boundary.py`）
2. **テナント分離**: あるスペースのデータビューに他スペースのデータは決して現れない
   （`test_data_api.py::test_view_is_scoped_to_space`）
3. **owner 専用操作**: 設定変更・メンバー管理・削除は owner ロールのみ
4. **取り込みの冪等性・名寄せ**: 同一エンティティの再取り込みは重複を作らない
   （`tests/unit/test_entity_resolver.py` / `test_ingestion_pipeline.py`）
5. **LLM 出力への防御**: 不正な形の LLM 出力でもパイプラインは壊れず劣化する
   （`test_marketing_agent.py` / アップロードの error 着地: `test_integration_upload.py`）
6. **API クライアント契約**: すべてのバックエンド呼び出しに Authorization と
   X-Space-Id が付与される（`frontend/lib/api.test.ts`）

細部が未確定の挙動は**特性化テスト**として現挙動を固定している。仕様を変える際は
テストを意図的に書き換えること（その差分が仕様決定の記録になる）。

## CI（.github/workflows/ci.yml）

PR（main 向け）で並列実行:

| ジョブ | 内容 |
|---|---|
| backend-test | ruff check / format + ユニットテスト |
| backend-integration | Firestore/Auth エミュレータ + `pytest -m integration` |
| frontend-build | lint + typecheck + Vitest + next build |
| e2e-smoke | エミュレータ + Playwright（失敗時 trace をアーティファクト保存） |

## テスト追加のガイドライン

- 新しい純粋ロジック → ユニットテスト（I/O を跨ぐ依存はフェイク/monkeypatch）
- 新しいルーター/エンドポイント → 統合テスト1本（`make_client` + `seeded_space` を再利用）
- 新しい画面・主要導線の変更 → 既存 E2E スモークが通ることを確認（E2E の追加は慎重に。
  遅く・壊れやすい層なので、下層でカバーできないものだけ）
- テストが書きづらいと感じたら: その場で純粋関数を抽出する小規模リファクタは推奨。
  大規模リファクタはテスト整備と混ぜない（検知の基準線が動くため）
