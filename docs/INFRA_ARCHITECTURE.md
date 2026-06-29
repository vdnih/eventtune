# インフラアーキテクチャ

## システム全体図

```
┌─────────────────────────────────────────────────────────────┐
│                        ユーザーブラウザ                         │
│  Next.js (App Router)                                        │
│  ├── Firebase Auth SDK      (ID Token 取得)                  │
│  └── REST/SSE クライアント   (チャット・取り込み・データ閲覧)     │
└───────────────────────────┬─────────────────────────────────┘
                             │ REST/SSE API (Bearer: Firebase ID Token)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  Cloud Run: mmg-api  [asia-northeast1]                       │
│  FastAPI + Google ADK                                        │
│  ├── Firebase Admin SDK  (ID Token 検証、Firestore 読み書き)  │
│  ├── ファイル取り込み      (multipart アップロードを直接受信)     │
│  └── Google ADK          (Vertex AI / Agent Engine 呼び出し)  │
└───────────────────────────┬─────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────────┐
        ▼                    ▼                        ▼
┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│ Firebase / GCP  │ │ Vertex AI (global)│ │ Agent Engine [us-central1]│
│ ├── Auth        │ │ gemini-3.1-flash- │ │ (ReasoningEngine 1 個)    │
│ ├── Firestore   │ │   lite (全用途)    │ │ ① Code Interpreter        │
│ └── App Hosting │ │ gemini-embedding- │ │    サンドボックスの親      │
│                 │ │   001 (埋め込み)   │ │ ② VertexAiSessionService  │
└─────────────────┘ └──────────────────┘ └──────────────────────────┘
```

> リージョンは2系統に分離する: Gemini 呼び出しは `vertex_ai_location=global`、Agent Engine
> （サンドボックス＋セッション）は `agent_runtime_location=us-central1`。Cloud Run 本体は
> `asia-northeast1`。詳細は ADR-009 / `config.py` / `scripts/provision_agent_engine.py`。

## Firebase サービス構成

### Authentication
- プロバイダー: **Google Sign-In**（主）、Email/Password（副）
- Anonymous Auth は使用しない（ユーザー識別必須のSaaS）
- カスタムクレームでプランティア管理: `plan: "free" | "pro" | "enterprise"`
- バックエンドでユーザー作成時に Admin SDK でカスタムクレームをセット

### Firestore
- データベース: `(default)`, リージョン: `asia-northeast1`
- モード: **ネイティブモード**
- マルチテナント: 全データを `spaces/{space_id}/` 配下に置く（パスプレフィックス分離）
- セキュリティルール方針（多層防御）:
  - 認可の真実は `spaces/{spaceId}/members/{uid}` の存在（membership）
  - **書き込みはすべて禁止**（クライアント SDK 直アクセス時の防御）。実書き込みは
    バックエンドの Admin SDK が「検証済み uid × membership」を確認してから行う
  - 読み取りはスペースのメンバーのみ、スペース配下の全ドキュメント

```
// firestore.rules（実体）
function isMember(spaceId) {
  return request.auth != null
    && exists(/databases/$(database)/documents/spaces/$(spaceId)/members/$(request.auth.uid));
}
match /spaces/{spaceId} {
  allow read: if isMember(spaceId);
  allow write: if false;
  match /{document=**} {
    allow read: if isMember(spaceId);
    allow write: if false;
  }
}
```

### Firebase Storage (= Google Cloud Storage)
- バケット: `{project-id}.appspot.com`
- アップロードパス: `uploads/{userId}/{projectId}/{filename}`
- ブラウザから Storage SDK で**直接アップロード**（API サーバーを経由しない）
- バックエンドは GCS パスのみ受け取り、Admin SDK / GCS クライアントで読み取る
- ライフサイクルルール:
  - `uploads/` 配下のオブジェクト: **7日後に自動削除**
  - 生成データは Firestore に永続化するため Storage には保存しない

```
// storage.rules の基本方針
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /uploads/{userId}/{projectId}/{filename} {
      allow read: if false;  // バックエンドが Admin SDK で読み取り
      allow write: if request.auth.uid == userId
                   && request.resource.size < 10 * 1024 * 1024;  // 10MB 上限
    }
  }
}
```

### Firebase App Hosting
- Next.js App Router の SSR をサポート（`output: 'export'` 不使用）
- リポジトリの `frontend/` を指定してデプロイ
- `/api/*` リクエストは Cloud Run の `mmg-api` サービスへリライト

## Cloud Run 構成

### サービス: `mmg-api`

```
サービス名:   mmg-api
リージョン:   asia-northeast1
CPU:          1 vCPU (always-allocated)  ← ADK ストリーミングのため常時割り当て
メモリ:       2Gi                          ← ADK エージェントコンテキストが大きい
最小インスタンス: 0 (ハッカソン) / 1 (本番)
最大インスタンス: 10
タイムアウト:  3600秒                      ← エージェント生成は数分かかる場合がある
同時リクエスト: 80
サービスアカウント: mmg-api-sa@{project-id}.iam.gserviceaccount.com
```

### サービスアカウント IAM ロール

| ロール | 用途 |
|-------|------|
| `roles/datastore.user` | Firestore 読み書き |
| `roles/storage.objectViewer` | GCS アップロードファイル読み取り |
| `roles/aiplatform.user` | Vertex AI（Gemini）/ Agent Engine（サンドボックス・セッション）呼び出し |
| `roles/firebase.sdkAdminServiceAgent` | Firebase Auth ID Token 検証 |
| `roles/secretmanager.secretAccessor` | Secret Manager から環境変数取得 |

### Dockerfile 構成方針

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## Vertex AI / Agent Engine 連携

### 推論エンドポイントを使用しない方針

Vertex AI の Online Prediction Endpoint（推論エンドポイント）は使用しない。常時課金を避け、
トークン従量課金のダイレクト呼び出し（`model=` にモデルID）を採用する。`model=` に
エンドポイント URI（`projects/.../endpoints/...`）を指定しない。

> 注（ADR-009 で更新）: 旧版は「`google-cloud-aiplatform` を依存に追加しない」と禁じていたが、
> Code Interpreter（Agent Engine サンドボックス）導入に伴い **撤回**。現在は `aiplatform`
> （`vertexai.Client` / `vertexai.types`）を前提に Agent Engine の sandboxes を直接叩く。
> 推論エンドポイントを使わない方針自体は維持する。

### モデル選択

全用途で **`gemini-3.1-flash-lite`** を使用する（分析・補完・メール生成・セグメント分類・
appeal_summary 生成）。埋め込みのみ **`gemini-embedding-001`**（768 次元）。
利用箇所: `marketing_agent.py` / `segmentation.py` / `semantic_search.py` /
`data_integration_agent.py` / `routers/integration.py` / `plans.py`。

### Agent Engine（ReasoningEngine）

`scripts/provision_agent_engine.py` で **1 個だけ**作成し、マネージドサービスとして2役で使う
（このバックエンドを Agent Engine に「デプロイ」はしない）:
1. **Code Interpreter サンドボックスの親**: `sandboxes.create` / `execute_code` を直接叩き、
   LLM 生成 Python を隔離・ステートフルに実行（ADK の CodeExecutor は使わない）。
2. **会話セッションのストア**: `VertexAiSessionService`。Cloud Run の再起動/オートスケールを
   跨いで session.state（サンドボックス名）を永続。

### ADC（Application Default Credentials）

Cloud Run 上ではサービスアカウントによる ADC が自動的に機能するため、API キーの管理は不要。
ローカル開発では `gcloud auth application-default login` を使用。

```python
# Agent Engine セッション接続例（marketing_agent.py）
from google.adk.sessions import VertexAiSessionService

session_service = VertexAiSessionService(
    project=settings.google_cloud_project,
    location=settings.agent_runtime_location,   # us-central1
    agent_engine_id=settings.agent_engine_id,   # 必須
)
```

## 認証フロー

```
1. ユーザーが「Googleでサインイン」をクリック
2. Firebase Auth SDK が Google OAuth ポップアップを開く
3. Firebase が ID Token (JWT、有効期限1時間) を発行
4. フロントエンドがトークンをメモリに保存（localStorage 不使用）

5. API リクエスト時:
   Authorization: Bearer {idToken}

6. Cloud Run (FastAPI) で検証:
   decoded = auth.verify_id_token(id_token)
   uid = decoded["uid"]
   plan = decoded.get("plan", "free")

7. UID を Firestore ドキュメントの ownerId として使用
8. カスタムクレーム "plan" でフィーチャーゲーティング
```

## ファイルアップロード / 取り込みフロー

```
1. ユーザーがチャット入力エリアのアップロードボタンでファイルを選ぶ
2. POST /api/integration/plan にファイルを送り、取り込みプランを提案
3. UploadConfirmModal でユーザーが内容を確認・承認
4. POST /api/integration/batches に multipart で直接アップロード（GCS 直アップロードは不使用）
   - hint（自然言語のリンク解決補正）を任意で添付
   - IntegrationJob を作成し BackgroundTask で取り込み開始
5. DataIntegrationAgent が解析→オントロジーへ分解・リンク解決→Firestore 書き込み
   - 取り込み時に appeal_summary / appeal_vector も付与
6. フロントは GET /api/integration/batches/{id} をポーリングして進捗を反映
```

## Secret Manager 管理環境変数

| 変数名 | 説明 |
|--------|------|
| `GOOGLE_CLOUD_PROJECT` | GCP プロジェクト ID |
| `VERTEX_AI_LOCATION` | Gemini 呼び出しのロケーション（既定 `global`）|
| `AGENT_ENGINE_RESOURCE_NAME` | Agent Engine の resource name（provision スクリプトの出力） |
| `AGENT_ENGINE_ID` | Agent Engine の ID（サンドボックス親 / セッションストア） |
| `AGENT_RUNTIME_LOCATION` | Agent Engine のリージョン（既定 `us-central1`）|
| `FIREBASE_PROJECT_ID` | Firebase プロジェクト ID |
| `FRONTEND_ORIGIN` | CORS 許可オリジン（Firebase App Hosting URL）|

> リージョンは2系統に分離する: Gemini 呼び出しは `VERTEX_AI_LOCATION=global`、Agent Engine
> は `AGENT_RUNTIME_LOCATION=us-central1`（サポート地域が限定的）。Cloud Run のデプロイ先
> `asia-northeast1` とも別であることに注意（`config.py` 参照）。
