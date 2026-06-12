# Agentic Marketing Mail Generator

展示会・イベントで収集したカオスなリストをチャットに投げるだけで、Pydanticオントロジーへ自動マッピングし、リードごとに個別最適化されたお礼メールを生成する **AIエージェントプラットフォーム**。

## コア体験

1. CSV / Excel をドラッグ＆ドロップ
2. **Ingestion Agent** が列名・表記ゆれを吸収し、型付きリード（`StructuredLead`）として Firestore に保存
3. チャットに「メールを生成して」と送るだけ
4. **Execution Agent** がセグメント別ルール + Chain-of-Thought でブロック構造メールを生成
5. 各ブロックの「AIの思考（`reason_for_inclusion`）」をUIで確認・CSVで出力

## アーキテクチャ概要（3層構造）

```
┌─────────────────────────────────────────────────────────┐
│  UI Layer: Next.js (App Router)                          │
│  ファイルアップロード + チャット駆動UI                        │
└──────────────────┬──────────────────────────────────────┘
                   │ REST API (FastAPI)
┌──────────────────▼──────────────────────────────────────┐
│  Agent Layer: Google ADK + Gemini 2.5 Flash              │
│  ① Ingestion Agent: CSV/Excel → StructuredLead[]        │
│  ② Execution Agent: StructuredLead → TotalTailoredEmail │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│  Semantic DB Layer: Pydantic (SSoT) + Firestore          │
│  ontology.py がシステム全体の型定義の単一真実源             │
└─────────────────────────────────────────────────────────┘
```

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| フロントエンド | Next.js 15 (App Router), Tailwind CSS |
| バックエンド | Python 3.12, FastAPI |
| AI オーケストレーション | Google ADK + Gemini 2.5 Flash (Vertex AI) |
| 型定義・オントロジー | Pydantic v2 (`ontology.py`) |
| 認証・DB・ストレージ | Firebase (Auth, Firestore, Storage, App Hosting) |
| インフラ | Google Cloud Run (asia-northeast1) |

## ディレクトリ構成

```
marketing-mail-generator/
├── frontend/                      # Next.js 15 (App Router)
│   ├── app/
│   │   ├── (auth)/login/
│   │   └── (app)/dashboard/       # チャット駆動メインUI
│   ├── components/
│   │   └── features/
│   │       ├── upload/FileDropzone.tsx
│   │       └── email/EmailBlockCard.tsx
│   └── package.json
├── backend/                       # Python FastAPI
│   ├── ontology.py                # Pydantic SSoT（全型定義）
│   ├── contents_library.py        # コンテンツ15件（ContentItemリスト）
│   ├── agents/
│   │   ├── ingestion_agent.py     # CSV/Excel → StructuredLead[] → Firestore
│   │   └── execution_agent.py     # StructuredLead → TotalTailoredEmail → Firestore
│   ├── routers/
│   │   ├── ingest.py              # POST /api/ingest, GET /api/batches/*
│   │   ├── execute.py             # POST /api/execute, GET /api/execute/*
│   │   └── generate.py            # 旧 CSV → CSV エンドポイント（互換維持）
│   ├── main.py
│   ├── Dockerfile
│   └── pyproject.toml
├── docs/
│   ├── SOFTWARE_ARCHITECTURE.md
│   ├── INFRA_ARCHITECTURE.md
│   ├── ADR.md
│   └── PM.md
├── sample_data/
│   └── manufacturing_dx_event_leads.csv   # テスト用サンプルデータ
├── firebase.json
└── firestore.rules
```

## ローカル開発セットアップ

> 前提: Node.js 20+, Python 3.12+, [uv](https://docs.astral.sh/uv/), Google Cloud CLI がインストール済みであること。

```bash
# 1. 依存関係のインストール
cd frontend && npm install
cd ../backend && uv sync

# 2. 環境変数の確認（既に .env が存在する場合はスキップ）
cp backend/.env.example backend/.env
# GOOGLE_CLOUD_PROJECT, FIREBASE_PROJECT_ID を設定

# 3. バックエンド起動
cd backend && uv run uvicorn main:app --reload --port 8000

# 4. フロントエンド起動（別ターミナル）
cd frontend && npm run dev
```

## 使い方

1. `http://localhost:3000` にアクセスしてGoogleログイン
2. `sample_data/manufacturing_dx_event_leads.csv` をドロップ
3. Ingestion Agent が自動起動 → 「X件取り込みました」とチャットに表示
4. 「メールを生成して」と送信 → Execution Agent が全リードのメールを生成
5. 「AIの思考を見る」でChain-of-Thoughtを確認
6. 「ダウンロード」でCSV出力

## ドキュメント

- [ソフトウェアアーキテクチャ](docs/SOFTWARE_ARCHITECTURE.md) — オントロジー設計、エージェント構成、APIルート、Firestoreスキーマ
- [インフラアーキテクチャ](docs/INFRA_ARCHITECTURE.md) — GCP/Firebase構成、Cloud Run設定、認証フロー
- [アーキテクチャ決定記録](docs/ADR.md) — 設計上の意思決定とその根拠
- [プロジェクト管理](docs/PM.md) — スコープ、マイルストーン、リスク管理
