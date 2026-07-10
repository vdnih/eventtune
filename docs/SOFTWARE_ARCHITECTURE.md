# ソフトウェアアーキテクチャ

> 本ドキュメントは ADR-008（OSI セマンティックレイヤー / フラットスキーマ）と ADR-009
> （Agent Engine サンドボックス型 Code Interpreter）以降の現行実装を反映する。設計思想の
> 背景は docs/SEMANTIC_LAYER.md / docs/PHILOSOPHY_AND_NAMING.md / docs/ADR.md を参照。

## コア設計原則: Pydantic オントロジー（Single Source of Truth）

`backend/ontology.py` がシステム全体の型定義の唯一の真実源。概念モデルの正典は
`backend/semantic/osi_event_marketing_v1.yml`（OSI v1.0）で、ontology.py はその物理実装。

データモデルは**星座型（ファクト・コンステレーション）**。持続する実体（マスタ）を、出来事
（ファクト）が結びつける。固定の課題ラベルは持たず、各実体の `appeal_summary`（関心・価値の
自然文要約）と `appeal_vector`（その埋め込み）の**意味的近接（コサイン類似度）**で「誰に何が
合うか」を表す（Semantic Affinity）。

```
マスタ（各 appeal_summary / appeal_vector を持つ）:
  Person       個人（旧 Contact を分解）。stage
  Account      企業マスター（Person を account_id で束ねる）
  Product      製品マスター
  Content      推薦可能なコンテンツ（資料・事例・セミナー等。旧 ContentAsset）
  Event        イベント。KPI・Survey 集計値を畳み込み保持（旧 EventKPI / SurveyResponse を統合）

ファクト（マスタ同士を結ぶ多対多）:
  EventAttendance   Person × Event（誰がどのイベントに参加したか）
  ProductInterest   Person × Product（誰がどの製品に関心を持つか）

セグメント / 成果物:
  Segment / SegmentSnapshot / SegmentAssignment   施策の分類軸と版管理つき割り当て（根拠必須）
  DeliverablePattern   バケット単位のひな型（pattern_id = "{bucket}__{format}"）
  Deliverable          生成成果物（format: EMAIL / TALK_SCRIPT / PROPOSAL）
  MarketingRun         組み立てジョブ

来歴 / 取り込み:
  IntegrationJob   取り込みジョブの稼働ログ（旧 integration_batches + data_lineage を統合）。
                   各レコードは source_job_id でこのジョブへ逆引きできる。
```

全 LLM 呼び出しは **`gemini-3.1-flash-lite`**（埋め込みのみ `gemini-embedding-001`）。

---

## エージェント構成

チャット駆動。バッチ2段（取り込み→生成）ではなく、ユーザーとの対話の中でエージェントが
自律的にツールを選んで進める。

### DataIntegrationAgent (`agents/data_integration_agent.py` + `ingestion/`)

カオスなファイル（CSV/Excel/テキスト/Word/PDF/PowerPoint。PDF・PowerPoint はテキスト抽出のみで
欠落リスクは Confirm 画面に注釈表示）をオントロジーへ分解・リンク解決して
書き込む。概念の正典は [INGESTION_MAPPING.md](INGESTION_MAPPING.md) / ADR-015。

| 項目 | 内容 |
|------|------|
| トリガー | `POST /api/integration/plan`（BatchPlan 生成）→ 確認 → `POST /api/integration/batches`（承認済み BatchPlan をそのまま実行。BackgroundTask + ハートビート/stale sweep） |
| 段階 | Read（source_records 着地）→ Understand（AI×1回）→ Confirm（人間）→ Interpret（`ingestion/engine.py` の機械適用。ai_parse 宣言列のみ軽量AI）→ Conform → Bind → Derive → Report |
| リンク解決 | 行の link_columns → 確認済み既定イベント（default_event）→ 保留（pending。ファクトを書かず理由を記録） |
| スペック駆動 | `ingestion/specs.py` の IngestionSpec レジストリからプロンプト・抽出スキーマ・変換・確定/結合順を導出（種別追加 = モデル + 1エントリ） |
| 意味レイヤー付与 | マスタは Conform で、person は Derive で `appeal_summary`/`appeal_vector` を付与（`semantic_search.build_appeal`） |
| 監査 | `IntegrationJob` に plan / transformations / skipped_records / resolved_links / report_markdown、`source_records` に全観測ブロックの行き先（bound/pending/skipped + 理由）を残す |

### MarketingAgent (`agents/marketing_agent.py`)

単一・汎用のエージェント。システムプロンプトには思想・オントロジー・ツール一覧のみを書き、
手順は固定しない。スペース束縛は `make_tools(db, space)` ファクトリで closure 捕捉する
（ツール引数に space_id は存在せず、他スペースへ到達不能＝最小権限の構造的強制）。

**ツール一覧（`make_tools`）**

| ツール | 用途 |
|--------|------|
| `get_space_data` | 全エンティティを Firestore→Pydantic→DataFrame→CSV にしてサンドボックスへ投入 |
| `run_python_code` | LLM 生成 Python をサンドボックスで実行（分析の実体。ADR-009） |
| `find_relevant_for_person` | Person の appeal_vector に意味的に近い contents/products/events を上位返す（コサイン類似度） |
| `save_report` | 分析・戦略レポートを `events/{event_id}/reports` に保存 |
| `define_segment` | 施策の分類軸（axes / buckets / criteria）を登録 |
| `assign_segment` | Person をバケットへ分類しスナップショット保存（決定論＋意味的近接＋軽量LLM、根拠必須） |
| `generate_patterns` | バケット単位でひな型を生成（pattern_id = `{bucket}__{format}`） |
| `run_assembly` | 分類×パターンから各 Person の Deliverable を決定論的に組み立て |

応答は SSE でストリーミング（`chat_stream`）。`run_python_code` の呼び出しと結果は
`code` / `code_result` イベントとして可視化する。

### Code Interpreter（ADR-009）

コード実行は ADK の `code_executor`（CodeAct）ではなく **`run_python_code` 関数ツール**で行う。
サンドボックスは Agent Engine 上にセッション毎に1つ作り（`sandboxes.create` / `execute_code`
直叩き）、名前を `tool_context.state["sandbox_name"]` に保持して再利用する（変数・ファイルが
持続するステートフル実行）。CSV はサンドボックスへ直接投入し、Parquet/ローカルファイルは使わない。

### 意味検索・分類の決定論レイヤー

| モジュール | 役割 |
|------------|------|
| `semantic_search.py` | 埋め込み（embed_text / embed_text_sync）、appeal_summary 生成、cosine / find_similar（決定論・総当たり） |
| `segmentation.py` | 構造化フィールドで自明な軸は決定論、残りは「appeal_vector ⇄ バケット代表ベクトルの近接」を一次候補に軽量LLMが appeal_summary で確認・確定。固定課題ラベルは主信号から退役 |

セッションは `VertexAiSessionService`（Agent Engine マネージドセッション）に保存し、Cloud Run の
オートスケール/再起動を跨いで session.state（サンドボックス名）を永続させる。

---

## FastAPI ルート一覧

すべてのルートで `Authorization: Bearer {firebase_id_token}` が必須。スペースは
`X-Space-Id` 等のコンテキスト（`dependencies.get_space_context`）で束縛する。

```
# Spaces（テナント / メンバー / 利用状況）— spaces.py
POST   /api/spaces                              # スペース作成
GET    /api/spaces                              # 自分が属するスペース一覧
GET/PATCH/DELETE /api/spaces/{id}               # 取得 / 更新 / 削除
GET/POST/PATCH/DELETE /api/spaces/{id}/members  # メンバー管理
GET    /api/spaces/{id}/usage                   # 月次の利用実績

# Integration（取り込み）— integration.py
POST   /api/integration/plan                    # 取り込みプラン提案
POST   /api/integration/batches                 # ファイルアップロード→取り込み開始（BackgroundTask）
GET    /api/integration/batches/{id}            # バッチ処理状況

# Marketing（チャット / 成果物）— marketing.py
POST   /api/marketing/chat                      # SSE チャット（MarketingAgent）
GET    /api/marketing/runs/{id}                 # 組み立てジョブの状況
GET    /api/marketing/runs/{id}/results         # 生成済み Deliverable 一覧
GET    /api/marketing/runs/{id}/export          # CSV 一括エクスポート

# Events（最小）— events.py
GET    /api/events                              # イベント一覧
POST   /api/events                              # イベント作成

# Data（汎用閲覧・読み取り専用）— data.py
GET    /api/data/collections                    # 閲覧可能なビュー一覧（左メニュー用）
GET    /api/data/{view_key}                     # ビューのドキュメント群（整形しない）
GET    /api/data/lineage/by-entity/{entity_id}  # source_job_id から由来ジョブを逆引き

GET    /health
```

データの**閲覧**は data.router に一本化した（オントロジー追加時は VIEWS に1行足すだけ）。
データの**編集**はチャットの AI エージェントに委ねる。

---

## Firestore データモデル（マルチテナント）

全データは `spaces/{space_id}/` 配下に置く（パスプレフィックス分離・AI 非依存のテナント束縛）。

```
spaces/{space_id}/
  persons/{person_id}
  accounts/{account_id}
  products/{product_id}
  contents/{content_id}
  events/{event_id}
      costs/{cost_id}          # 費用明細（CostItem）
      reports/{report_id}      # save_report の出力
  event_attendances/{attendance_id}
  product_interests/{interest_id}
  segments/{segment_id}
      snapshots/{snapshot_id}
          assignments/{person_id}   # SegmentAssignment（reason 必須）
      patterns/{bucket}__{format}   # DeliverablePattern
  marketing_runs/{run_id}
      deliverables/{deliverable_id} # Deliverable
  integration_jobs/{job_id}         # IntegrationJob（来歴の逆引き元）
```

各 master/fact レコードは `source_job_id`（= integration_jobs.job_id）を inline 保持し、
そこから取り込みジョブへ O(1) で逆引きする（data.router の lineage）。

---

## ディレクトリ構成（要点）

```
marketing-mail-generator/
├── frontend/
│   ├── app/(app)/
│   │   ├── layout.tsx                  # 認証ガード + ヘッダ（エージェント / データ ナビ）
│   │   ├── dashboard/page.tsx          # 全画面チャット UI（アップロードはチャット入力欄）
│   │   ├── explorer/page.tsx           # 3ペイン汎用データブラウザ（/api/data/* 駆動）
│   │   ├── spaces/new/page.tsx
│   │   └── settings/{space,members,usage}/page.tsx
│   ├── components/
│   │   ├── ui/{DataTable,Drawer,format}.tsx   # モデル非依存・オントロジー安全
│   │   └── features/agent/DeliverableCard.tsx # 汎用 AI 成果物カード
│   └── lib/{firebase,api,space-context}.ts
│
└── backend/
    ├── ontology.py                     # Pydantic SSoT
    ├── semantic/osi_event_marketing_v1.yml  # 概念モデルの正典（OSI v1.0）
    ├── space.py / space_data.py        # テナント束縛 / 全エンティティのロード
    ├── semantic_search.py              # 埋め込み・appeal_summary・cosine/find_similar
    ├── segmentation.py                 # セグメント分類（決定論＋意味的近接＋軽量LLM）
    ├── agents/
    │   ├── data_integration_agent.py   # 取り込み
    │   ├── ontology_mapper.py          # リンク解決・マッピング
    │   └── marketing_agent.py          # チャット・分析・生成（Code Interpreter）
    ├── routers/{spaces,integration,marketing,events,data}.py
    ├── scripts/provision_agent_engine.py   # Agent Engine の一回限り provision
    ├── main.py / config.py / dependencies.py / metering.py / plans.py
    └── pyproject.toml
```

---

## 主要フロントエンドコンポーネント

### `dashboard/page.tsx`
全画面チャット。アップロードボタンはチャット入力エリアに統合し、`POST /api/integration/plan`
で取り込みプランを提案 → `UploadConfirmModal` で確認 → `POST /api/integration/batches`。
個別カスタマイズは SSE（`/api/marketing/chat`）でエージェントが進め、`run_assembly` 検出後に
`/api/marketing/runs/{id}` をポーリングして成果物を `DeliverableCard` で表示する。

### `explorer/page.tsx`
3ペインの汎用データブラウザ。左=コレクションナビ（`/api/data/collections`）、中央=`DataTable`
（`/api/data/{key}`）、右=詳細＋「由来を追う」（`/api/data/lineage/by-entity/{id}`）。
`DataTable` / `Drawer` / `format` はモデル非依存で、オントロジー変更に追従不要。

### `DeliverableCard`
生成成果物（メール等）を format 非依存で表示。ブロック単位の表示と
`reason_for_inclusion`（Auditable AI の根拠）のトグルを持つ。
