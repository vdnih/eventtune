# インフラアーキテクチャ

## システム全体図

```
┌─────────────────────────────────────────────────────────────┐
│                        ユーザーブラウザ                         │
│  Next.js (App Router)                                        │
│  ├── Firebase Auth SDK      (ID Token 取得)                  │
│  ├── Firebase Storage SDK   (ファイル直接アップロード)           │
│  └── Firestore SDK          (onSnapshot リアルタイム読み取り)   │
└───────────────────────────┬─────────────────────────────────┘
                             │ REST API (Bearer: Firebase ID Token)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  Cloud Run: mmg-api  [asia-northeast1]                       │
│  FastAPI + Google ADK                                        │
│  ├── Firebase Admin SDK  (ID Token 検証、Firestore 書き込み)  │
│  ├── GCS Client          (アップロードファイル読み取り)          │
│  └── Google ADK          (Vertex AI エージェント呼び出し)       │
└───────────────────────────┬─────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
┌─────────────────────┐     ┌──────────────────────────────┐
│  Firebase / GCP     │     │  Vertex AI                   │
│  ├── Auth           │     │  Gemini 2.0 Flash (分析・補完) │
│  ├── Firestore      │     │  Gemini 2.5 Pro  (メール生成)  │
│  ├── Storage (GCS)  │     │  ADK Session Service         │
│  └── App Hosting    │     └──────────────────────────────┘
└─────────────────────┘
```

## Firebase サービス構成

### Authentication
- プロバイダー: **Google Sign-In**（主）、Email/Password（副）
- Anonymous Auth は使用しない（ユーザー識別必須のSaaS）
- カスタムクレームでプランティア管理: `plan: "free" | "pro" | "enterprise"`
- バックエンドでユーザー作成時に Admin SDK でカスタムクレームをセット

### Firestore
- データベース: `(default)`, リージョン: `asia-northeast1`
- モード: **ネイティブモード**
- セキュリティルール方針:
  - フロントエンドからの**書き込みはすべて禁止**
  - 読み取りは認証済みユーザーが自身のドキュメントのみ許可
  - すべての書き込みはバックエンドの Admin SDK 経由
- TTL ポリシー: `generation_jobs` コレクションは 30日後に自動削除

```
// firestore.rules の基本方針
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // ユーザードキュメント: 本人のみ読み取り可
    match /users/{userId} {
      allow read: if request.auth.uid == userId;
      allow write: if false;  // 書き込みは Admin SDK のみ
    }
    // プロジェクト: オーナーのみ読み取り可
    match /projects/{projectId} {
      allow read: if request.auth != null
                  && resource.data.ownerId == request.auth.uid;
      allow write: if false;
      // サブコレクション (contacts, emails) も同様
      match /{subcollection}/{docId} {
        allow read: if request.auth != null
                    && get(/databases/$(database)/documents/projects/$(projectId)).data.ownerId == request.auth.uid;
        allow write: if false;
      }
    }
    // generation_jobs: オーナーのみ読み取り可
    match /generation_jobs/{jobId} {
      allow read: if request.auth != null
                  && resource.data.ownerId == request.auth.uid;
      allow write: if false;
    }
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
| `roles/aiplatform.user` | Vertex AI / ADK 呼び出し |
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

## Vertex AI 連携

### 推論エンドポイントを使用しない方針

**このプロジェクトでは Vertex AI の Online Prediction Endpoint（推論エンドポイント）を使用しない。**

推論エンドポイントはデプロイ中は無操作でも常時課金が発生するため、従量課金のダイレクト呼び出しを採用する。

| 方式 | model 指定 | 課金 | 本プロジェクト |
|------|-----------|------|--------------|
| ダイレクト呼び出し | `"gemini-2.0-flash"` などモデルID | トークン従量課金のみ | ✅ 採用 |
| 推論エンドポイント | `projects/.../endpoints/12345` | 起動中は常時課金 | ❌ 不使用 |

**守るべきルール:**
- `model=` にエンドポイントURI（`projects/.../locations/.../endpoints/...`）を指定しない
- `google-cloud-aiplatform` パッケージを依存関係に追加しない（`EndpointServiceClient` / `PredictionServiceClient` が使えてしまうため）
- `VertexAiSessionService` を導入する場合も `endpoint_id` ではなく `model` パラメータ（モデルID）を使う

### モデル選択

| フェーズ | モデル | 理由 |
|---------|-------|------|
| データ分析 (`DataAnalystAgent`) | `gemini-2.0-flash` | 高速・低コスト、構造化抽出に十分 |
| 補完チャット (`SupplementAgent`) | `gemini-2.0-flash` | 対話型、レイテンシ重視 |
| メール生成 (`EmailWriterAgent`) | `gemini-2.5-pro` | 高品質なアウトプットが最優先 |

> ハッカソンのコスト管理として `gemini-2.5-pro` を `gemini-2.0-flash` に差し替えることも可能。

### ADC（Application Default Credentials）

Cloud Run 上ではサービスアカウントによる ADC が自動的に機能するため、API キーの管理は不要。ローカル開発では `gcloud auth application-default login` を使用。

```python
# ADK + Vertex AI 接続例
import vertexai
from google.adk.sessions import VertexAiSessionService

vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=VERTEX_AI_LOCATION)
session_service = VertexAiSessionService(
    project=GOOGLE_CLOUD_PROJECT,
    location=VERTEX_AI_LOCATION
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

## ファイルアップロードフロー

```
1. ユーザーが FileDropzone にファイルをドラッグ＆ドロップ
2. Firebase Storage SDK で直接 GCS にアップロード
   - パス: uploads/{userId}/{projectId}/{timestamp}_{filename}
   - uploadBytesResumable でプログレス表示
3. アップロード完了後、フロントエンドが POST /api/v1/projects/{id}/upload を呼び出し
   - ボディ: { gcsPath: "uploads/...", uploadBatchId: "uuid" }
4. バックエンドが GCS からファイルを読み取り
5. DataAnalystAgent が解析を開始
6. Firestore の generation_jobs/{jobId} にステータスを書き込み
7. フロントエンドの onSnapshot がリアルタイムで進捗を反映
```

## Secret Manager 管理環境変数

| 変数名 | 説明 |
|--------|------|
| `GOOGLE_CLOUD_PROJECT` | GCP プロジェクト ID |
| `VERTEX_AI_LOCATION` | Vertex AI リージョン（`us-central1`）|
| `FIREBASE_PROJECT_ID` | Firebase プロジェクト ID |
| `FRONTEND_ORIGIN` | CORS 許可オリジン（Firebase App Hosting URL）|

> `VERTEX_AI_LOCATION` は `us-central1` を推奨（Gemini 2.5 Pro の提供リージョンが限定的なため）。バックエンドのデプロイ先（`asia-northeast1`）とは異なることに注意。
