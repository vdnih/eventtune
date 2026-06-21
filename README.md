# Event Marketing AI Platform

展示会・セミナー・イベントで集めたカオスなマーケティングデータを **オントロジー** に統合し、
AIエージェントがその上でマーケティング活動（個別フォローアップメール生成・イベント振り返り分析・
戦略レポート）を行う **マルチテナント SaaS プラットフォーム**。

単なるメール生成ツールではない。イベントを中心軸に、ハウスリスト・KPI・費用・アンケートを束ね、
「相手の状況・課題・温度感」をAIが読み解いてふさわしい構成と言葉を選ぶ。個別カスタマイズは
**Static Core & Dynamic Context**（不変のコア＝自社の機能・価値は固定し、動的な文脈＝相手の悩みだけを
AIが最適化する）という設計思想に基づく（[docs/MARKETING_PHILOSOPHY.md](docs/MARKETING_PHILOSOPHY.md)）。

## コア体験

1. **スペース**（テナント）を作成 — データ・課金はスペース単位で構造的に分離
2. CSV / Excel やイベント概要・KPI・アンケートのテキストを**複数まとめてアップロード**
3. **DataIntegrationAgent** が列名・表記ゆれを吸収し、来歴（DataLineage）付きで
   オントロジー（`Event` / `Contact` / `EventKPI` / `SurveyResponse` / `CostItem` / `ContentAsset`）へ統合
4. チャットで **MarketingAgent** に指示（例: 「このイベントの参加者にお礼メールを」）
5. **セグメント方式 + HIL** で個別対応 — AIが軸を設計 → 人が承認 → 分類 → バケット別パターン生成 →
   各メールは決定論的に組み立て（高速・低コスト）。各ブロックの `reason_for_inclusion`（AIの判断根拠）を確認・CSV出力

## アーキテクチャ概要（3層 / 技術スタックの層）

```
┌──────────────────────────────────────────────────────────┐
│  UI Layer: Next.js 15 (App Router, SSR)                   │
│  スペース管理 + ファイルアップロード + チャット駆動UI         │
└──────────────────┬───────────────────────────────────────┘
                   │ REST / SSE (FastAPI)
┌──────────────────▼───────────────────────────────────────┐
│  Layer 3: Marketing Agent (Google ADK + Gemini)           │
│  MarketingAgent（単一・汎用 / Agent + Tools）              │
│   入口 chat_stream（SSE）。タスクは指示で切替              │
│   個別対応はセグメント方式: define_segment / assign_segment │
│   / generate_patterns / run_assembly                      │
├──────────────────────────────────────────────────────────┤
│  Layer 2: Event Ontology — Pydantic (SSoT) + Firestore    │
│  ontology.py が全型定義の単一真実源（Event 中心）          │
├──────────────────────────────────────────────────────────┤
│  Layer 1: Data Integration (DataIntegrationAgent)         │
│  パスA 表形式: run_schema_mapper / パスB 非構造化: run_document_extractor │
│   → OntologyMapper（決定論的Python変換）                   │
└──────────────────────────────────────────────────────────┘
```

> マーケティングの「情報3階層（L1大黒柱 / L2中柱 / L3ドア）」は、上記の*技術スタックの層*とは別の概念。
> [docs/MARKETING_PHILOSOPHY.md](docs/MARKETING_PHILOSOPHY.md) を参照。

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| フロントエンド | Next.js 15 (App Router, SSR), Tailwind CSS |
| バックエンド | Python 3.12, FastAPI |
| AI オーケストレーション | Google ADK + Gemini 3.1 Flash Lite (Vertex AI) |
| 型定義・オントロジー | Pydantic v2 (`ontology.py`) |
| 認証・DB・ストレージ | Firebase (Auth, Firestore, Storage, App Hosting) |
| インフラ | Google Cloud Run (asia-northeast1) |

## ディレクトリ構成

```
marketing-mail-generator/
├── frontend/                          # Next.js 15 (App Router, SSR)
│   ├── app/
│   │   ├── (auth)/login/
│   │   └── (app)/
│   │       ├── dashboard/             # チャット駆動メインUI
│   │       ├── spaces/new/            # スペース作成
│   │       └── settings/{space,members,usage}/  # スペース設定・メンバー・使用量
│   ├── components/features/
│   │   ├── upload/FileDropzone.tsx
│   │   ├── email/EmailBlockCard.tsx
│   │   └── explorer/EventDataPanel.tsx
│   └── package.json
├── backend/                           # Python FastAPI
│   ├── ontology.py                    # Pydantic SSoT（全型定義）
│   ├── space.py                       # SpaceContext（テナント束縛アクセス）
│   ├── segmentation.py                # セグメント分類（決定論＋軽量AI）
│   ├── metering.py / plans.py         # 使用量計測・クレジット換算
│   ├── agents/
│   │   ├── data_integration_agent.py  # Layer1: CSV/Excel・テキスト → オントロジー
│   │   ├── ontology_mapper.py         # ステージ2: 決定論的変換（AI不使用）
│   │   └── marketing_agent.py         # Layer3: MarketingAgent（Agent + Tools）
│   ├── routers/
│   │   ├── spaces.py                  # /api/spaces
│   │   ├── integration.py             # /api/integration（バッチ取り込み）
│   │   ├── marketing.py               # /api/marketing（チャット・ラン）
│   │   ├── segments.py                # /api/segments（成果物の閲覧・編集）
│   │   └── events.py                  # /api/events（オントロジー直接参照）
│   ├── main.py
│   ├── Dockerfile
│   └── pyproject.toml
├── docs/
│   ├── MARKETING_PHILOSOPHY.md        # マーケ思想（Static Core & Dynamic Context）
│   ├── PHILOSOPHY_AND_NAMING.md       # システム思想・命名規約
│   ├── SOFTWARE_ARCHITECTURE.md
│   ├── INFRA_ARCHITECTURE.md
│   ├── ADR.md
│   └── PM.md
├── sample_data/                       # テスト用サンプル（リストCSV・イベント概要・費用・アンケート等）
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

1. `http://localhost:3000` にアクセスして Google ログイン
2. **スペースを作成**（初回）— 以降のデータ・課金はこのスペース配下に分離される
3. `sample_data/` のリストCSVやイベント概要テキストを**まとめてドロップ** → DataIntegrationAgent が
   バッチ取り込み →「X件取り込みました」とチャットに表示
4. チャットで「このイベントの参加者にお礼メールを送りたい」等を指示
5. MarketingAgent が**セグメント軸を提案** → 承認 → 分類 → パターン生成 → 各ゲートで確認（HIL）→
   `run_assembly` で全件を決定論的に組み立て
6. 「AIの思考」で `reason_for_inclusion` を確認、「ダウンロード」で CSV 出力

## ドキュメント

- [マーケティング設計思想](docs/MARKETING_PHILOSOPHY.md) — Static Core & Dynamic Context、情報3階層、AI生成のガードレール
- [システム思想と命名規約](docs/PHILOSOPHY_AND_NAMING.md) — 設計原則、オントロジー、正規命名一覧
- [ソフトウェアアーキテクチャ](docs/SOFTWARE_ARCHITECTURE.md) — オントロジー設計、エージェント構成、APIルート、Firestoreスキーマ
- [インフラアーキテクチャ](docs/INFRA_ARCHITECTURE.md) — GCP/Firebase構成、Cloud Run設定、認証フロー
- [アーキテクチャ決定記録](docs/ADR.md) — 設計上の意思決定とその根拠
- [プロジェクト管理](docs/PM.md) — スコープ、マイルストーン、リスク管理
