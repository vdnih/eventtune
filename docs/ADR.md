# Architecture Decision Records (ADR)

アーキテクチャに関する意思決定とその根拠を記録する。

---

## ADR-001: リポジトリ構成 — シンプルな2ディレクトリ構成を採用

**ステータス**: 採用

**背景**:
`frontend/` (Next.js) と `backend/` (Python) という異なる言語・ランタイムを持つ2つのサービスをどう管理するか検討した。

**決定**:
`frontend/` と `backend/` をリポジトリルートに並列配置する。モノレポツール（Turborepo、Nx 等）は使用しない。

**理由**:
- フロントエンドとバックエンドは言語が異なり（TypeScript vs Python）、ビルドツールチェーンを統一するメリットがない
- 型の共有が必要な場合は OpenAPI スキーマ（FastAPI の自動生成）から TypeScript 型を生成すれば足りる
- モノレポツールは設定・学習コストが高く、ハッカソン時間制約下では不要な複雑性
- デプロイも独立（frontend は Firebase App Hosting、backend は Cloud Run）のため、統合ビルドパイプラインは不要

**結果**:
- CI/CD は `frontend/` と `backend/` で独立したワークフローを持つ
- 共有設定ファイル（`firebase.json`, `firestore.rules`）はリポジトリルートに配置

---

## ADR-002: バックエンドサービス構成 — Cloud Run シングルサービス

**ステータス**: 採用

**背景**:
メール生成パイプラインは長時間実行タスク（数分規模）を含む。オプションとして以下を検討した:
- (A) Cloud Run シングルサービス（API + エージェント実行を同一コンテナ）
- (B) Cloud Run マルチサービス（API ゲートウェイ + ワーカー分離）
- (C) Cloud Functions（API）+ Cloud Run（エージェント）

**決定**:
Cloud Run シングルサービス（オプション A）を採用。

**理由**:
- Cloud Run は最大3600秒のリクエストタイムアウトに対応しており、ADK エージェントの実行時間を吸収できる
- シングルサービスはサービス間認証、追加 IAM バインディング、デプロイ設定の重複がなく運用が単純
- ADK の実行は HTTP リクエストスコープ内で完結（Firestore に進捗を書き込みながらバックグラウンドスレッドで実行）
- Cloud Functions Gen 2 はタイムアウトと Cold Start 特性が ADK ワークロードに不適
- `always-allocated` CPU により ADK のストリーミング中も CPU 割り当てが維持される

**将来の移行パス**:
ユーザー規模が拡大した場合、Cloud Tasks キューを導入してジョブをワーカー Cloud Run サービスに委譲する。FastAPI のルートコントラクトは変更不要。

---

## ADR-003: Google ADK エージェントトポロジー

**ステータス**: 採用

**背景**:
AIパイプラインは3フェーズある: (1)データ解析、(2)補完チャット、(3)メール生成。これをどう設計するかの選択肢:
- (A) 1つのモノリシックエージェント
- (B) 3つの独立した関数呼び出し（ADK なし）
- (C) `SequentialAgent` によるバッチパイプライン + 補完フェーズは別セッション LlmAgent

**決定**:
オプション C を採用。バッチパイプライン（解析→生成）は `SequentialAgent`、補完チャットは別途セッション付き `LlmAgent` で管理。

**理由**:
- `SequentialAgent` はフェーズ間のステート受け渡しと構造化ツール呼び出しログを提供し、デバッグが容易
- 補完フェーズは本質的に「ヒューマンインザループ」（ユーザーが何度もメッセージを送る）であり、バッチ型シーケンスに収まらない。ADK のセッション機能を使って1コンタクトにつき1セッションを維持する
- `EmailWriterAgent` は生ファイルではなく Firestore に書き込まれた構造化 `ContactInsight` を受け取るため、部分失敗後の再実行が安全

**セッション管理**:
- ハッカソン: `InMemorySessionService`（Cloud Run インスタンス依存）
- 本番: `VertexAiSessionService`（マルチインスタンス対応）

---

## ADR-004: エビデンスベースドマーケティング原則の統合方法

**ステータス**: 採用

**背景**:
プロダクトの差別化ポイントは「汎用 LLM を超える品質」。商談メモを渡して「メールを書いて」と指示するだけでは差別化にならない。品質をどう担保するかを設計する必要があった。

**決定**:
3層プロンプト設計を採用する。詳細は [SOFTWARE_ARCHITECTURE.md の「プロンプト3層設計」](SOFTWARE_ARCHITECTURE.md) を参照。

**各層の役割**:
1. **System Prompt**: Ehrenberg-Bass 理論に基づくマーケティング専門家ペルソナ、メンタルアベイラビリティ・一貫性・誠実な便益訴求を非交渉のルールとして設定
2. **Brand Context**: プロジェクトごとのブランドガイドライン（トーン・禁止ワード・コアメッセージ）— マーケターが1回設定
3. **Contact Personalization**: AIが抽出した個人の洞察（ペインポイント・文脈・推奨アプローチ）— 完全自動

**テンプレートRAG**:
`templates` コレクションにマーケター監修のテンプレートを格納し、`load_templates_tool` が条件マッチングで取得。ベクター検索なしでもキーワードベースで十分（ハッカソンフェーズ）。本番ではVertex AI Vector Search または Firestore ベクター検索に移行可能。

**理由**:
- Layer 1 と Layer 2 がなければ生成品質は汎用 LLM と変わらない
- Layer 2（ブランドコンテキスト）が SaaS として課金根拠になる — プロジェクト設定に投資するほど出力品質が上がる
- テンプレートを Firestore で管理することで、エンジニアなしでマーケターが品質をチューニングできる

---

## ADR-005: Next.js レンダリング戦略 — SSR 採用

**ステータス**: 採用

**背景**:
Next.js には `output: 'export'`（完全静的）と SSR（サーバーサイドレンダリング）の選択肢がある。

**決定**:
SSR を採用し、Firebase App Hosting でホストする。`output: 'export'` は使用しない。

**理由**:
- アプリケーションは認証ゲート付きルート、ユーザーごとのデータ、リアルタイム Firestore 購読を持つ。これらはすべて Server Components + SSR が有利
- Firebase App Hosting（2025年GA）が Next.js App Router を直接サポートしており、SSR のホスティングが容易
- `output: 'export'` ではサーバーサイドでの認証チェックができず、クライアント側の初期描画にちらつきが生じる
- 将来的な SEO 対応（ランディングページ等）が必要になった際にも対応可能

**結果**:
- Firebase App Hosting の `apphosting.yaml` で `backend/` ではなく `frontend/` を指定
- `/api/*` リクエストは Cloud Run `mmg-api` へリライト

---

## ADR-006: ファイルアップロード方式 — ブラウザから GCS 直接アップロード

**ステータス**: 採用

**背景**:
ユーザーのファイルをバックエンド経由でアップロードする方法（API プロキシ）と、ブラウザから直接 GCS へアップロードする方法を比較した。

**決定**:
Firebase Storage SDK を使ってブラウザから GCS へ直接アップロードし、バックエンドには GCS パスのみを通知する。

**理由**:
- ファイルのバイナリを API サーバー経由で転送すると、Cloud Run のメモリ使用量が増え、アップロード時間が2倍になる
- Firebase Storage SDK の `uploadBytesResumable` がプログレス表示・リトライを標準提供
- API サーバーはステートレスを維持でき、スケールアウトが容易
- セキュリティ: `storage.rules` でユーザーは自身のパス配下にのみ書き込み可能。バックエンドは Admin SDK で読み取り専用

---

## ADR-007: 個別カスタマイズに Static Core & Dynamic Context を採用

**ステータス**: 採用

**背景**:
プロダクトが多機能プラットフォームへ進化し、また生成AIにより顧客1社ごとの 1to1 カスタマイズが限界費用ゼロで
可能になった。この状況では「圧倒的なパーソナライズ」と「揺るぎないブランド一貫性」を両立させる枠組みが要る。
従来の STP（静的属性で少数セグメントに絞る）では、AIが文脈に過剰に迎合して**存在しない機能を語る／複数機能を
押し売りする**といったブランド崩壊が起きうる。これをどう制御するかを決める必要があった。

**決定**:
マーケ設計思想 **「Static Core & Dynamic Context（不変のコアと動的な文脈）」** を共通言語として採用する。
情報を**情報3階層**でモデリングし、AIが生成してよい範囲を構造的に区切る:

- **L1 大黒柱 / L2 中柱（Static Core）** = プラットフォーム指針・機能の本質価値。**AIは書き換えない**。
- **L3 ドア（Dynamic Context）** = ターゲット別の悩み（CEP）。**AIが無数に生成してよい**。

その上で、AI生成に **3つのガードレール**を**プロンプトレベル（ソフト強制）**で課す:
①解決手段（コア）の捏造禁止、②複数機能の押し売り禁止（1機能 × 1CEP）、③独自ブランド資産（DBA）の維持。

思想の正典は [MARKETING_PHILOSOPHY.md](MARKETING_PHILOSOPHY.md)。システムへの落とし方は
[PHILOSOPHY_AND_NAMING.md 原則7](PHILOSOPHY_AND_NAMING.md)、実装は
[`backend/agents/marketing_agent.py`](../backend/agents/marketing_agent.py) の `_SYSTEM_PROMPT` および
`_generate_one_pattern` の【必須ルール】。

**Static Core の真実源（当面）**:
L1/L2 の構造化マスター専用モデルはまだ持たない。当面、機能・価値の事実上の真実源は
`ContentAsset`（`content_assets` コレクション）と `Product` enum とし、AIはここに帰結する範囲でのみ
機能・効果を語る。

**理由**:
- ソフト強制（プロンプト）を選ぶのは、原則7の「定型作業を過剰にプログラム化しない」「意味判断はAI」に整合するため。
  Python での機能数検証・再生成は導入しない。
- 違反を完全には防げないトレードオフは、原則7の **HIL（各ゲートでの人間承認）** で担保する
  （特に `run_assembly` は明示承認必須）。
- 本 ADR は ADR-004（エビデンスベースドマーケティング原則）の EBM（メンタルアベイラビリティ・
  一貫性）を、情報階層（Static/Dynamic）として一般化したものである。

**結果 / 将来課題**:
- 今回はドキュメント体系化（MARKETING_PHILOSOPHY 新設）＋プロンプトのガードレール実装にとどめる。
- L1/L2 の構造化マスターモデル、ADR-004 の Brand Context（トーン・禁止ワード・コアメッセージ）の
  構造化と注入機構、その入力UIは将来課題（実装時に `ontology.py` への新モデル追加と `make_tools` での注入が必要）。
- パブリック領域（Web/AEO）の「構造の全出し」は思想として明記したが未実装（現プロダクトはプライベート領域＝
  1to1メールを実装）。

---

## ADR-008: OSI セマンティックレイヤー採用 / Event-Centric 撤回

**ステータス**: 採用

**背景**:
データモデルに 2 つの構造的問題があった。(1) 同じスキーマが Pydantic（`ontology.py`）・AIプロンプト文・
決定論マッパーの enum マップ・フロント TS の **4 箇所に手書きで多重定義**され、変更時にずれた。
(2) モデルが **フラットかつ Event 中心** で、`Contact` が「個人＋企業＋興味製品＋参加イベント」を 1 実体に
詰め込んでいたため、`業種 × イベント × 製品` のようなマルチホップ分析を構造的に表現できなかった。
プロダクトはリリース前であり、互換性を捨てた抜本再設計が可能だった。

**決定**:
業界標準の考え方 **OSI（Open Semantic Interchange）v1.0** に倣い、データの「意味」を 1 つの YAML
（[`backend/semantic/osi_event_marketing_v1.yml`](../backend/semantic/osi_event_marketing_v1.yml)）に
**概念モデルの単一の思想源**として集約する。あわせて **ファクト・コンステレーション（星座型）** を採用し、
基底を **5 個のマスタ系 dataset（persons / accounts / events / products / contents）** へ移す。

これに伴い **[PHILOSOPHY_AND_NAMING.md](PHILOSOPHY_AND_NAMING.md) の「原則1: Event-Centric」を撤回**する。
Event は 5 マスタの 1 つにすぎず、唯一のルートではない。

主要な設計判断:
1. **5 コアコンポーネントのみ**（datasets / dimensions / metrics / relationships / context）で記述。
2. **物理層と意味層の分離**: SQL を使わず Firestore に保存するため `table:` ではなく
   `physical: {collection, id}` を宣言。`name`/`description`/`context` からは技術用語を排除する。
3. **metrics / relationships はセマンティック宣言のみ**: Firestore は JOIN/`count_distinct` を
   実行しないため、これらは AI へのコンテキストとして供給し、実集計・類似度計算は決定論 Python・ツールが担う。
4. **YAML は設計仕様書（手書き同期）**: ランタイムでロードしない（PyYAML 依存なし）。Pydantic・
   Firestore パスは YAML から手で導出し、整合はレビューと任意の整合テストで担保する。
5. **顧客の関心はベクトルで表現する（課題の第一級化は撤回）**: 当初案の `challenges` マスタ＋
   `person/product/content_challenges` 多対多ブリッジは **不採用**。理由はブリッジ肥大と、固定ラベルでは
   パターンマッチしかできず課題に収まらない関心・文脈を表せないこと。代わりに `persons`/`events`/
   `contents`/`products` に **`appeal_summary`（監査可能な要約テキスト）＋ `appeal_vector`（埋め込み）** を持たせ、
   `relationships.semantic_affinity`（コサイン類似度）で「この人に合うもの」を引く。類似度は Firestore の
   ベクトルインデックス・`find_nearest` を使わず **決定論 Python の総当たり**（`backend/semantic_search.py`）で
   計算する（判断3の方針と一致）。これは ADR-007 の L3=CEP（動的文脈）を、固定ラベルでなく連続的な
   意味空間として接地するものである。
6. **旧 Contact の分解**: `persons` ＋ `accounts` ＋ `event_attendances` ＋ `product_interests` へ正規化
   （課題・関心は `persons.appeal_summary`/`appeal_vector` が担う）。`EventKPI`/`SurveyResponse`/
   `CostItem`(集計) は `events` の metrics へ畳む。
7. **セグメントは動的定義＋静的スナップショット**: `segments`（フィルタ定義＝動的）に対し、施策時点の
   確定メンバーを `segment_snapshots`（複数版）として凍結し、`segment_assignments` は snapshot 配下に持つ。
   `marketing_run` は使用 snapshot を参照。動的セグメントと施策時点の確定メンバーは別物で、いずれも残す。
8. **成果物は `format` を持つ `deliverables` に一般化**: メールは個別カスタマイズ成果物の一形態。
   `ComposedEmail`→`Deliverable`（`format`=EMAIL/TALK_SCRIPT/PROPOSAL…）を **person 単位**で保存し、
   バケット×format の**雛形**は `DeliverablePattern`（`segments/{sid}/patterns/{bucket}__{format}`）に持つ。
9. **来歴は各データに inline、独立オブジェクトは稼働ログに**: `DataLineage`→`IntegrationJob`
   （DataIntegrationAgent の処理ジョブログ）。各 master/fact レコードに `source_job_id`/`source_file_id` を
   持たせ、データの出自はデータ自身から逆引きする。

思想の正典は [SEMANTIC_LAYER.md](SEMANTIC_LAYER.md)。命名・物理への落とし方は
[PHILOSOPHY_AND_NAMING.md](PHILOSOPHY_AND_NAMING.md)。

**理由**:
- 多重定義を YAML 起点の手書き同期へ集約し、意味の真実源を 1 つにする。
- 正規化（星座型）により、裏側の保存構造を意識しない AI のマルチホップ推論が構造的に可能になる。
- 関心をベクトルで表すことで、ADR-007 の Static Core & Dynamic Context が「person の関心 → 意味的に
  近い 機能 / 素材」の連続的な接地として実現する。固定ラベルの課題ブリッジでは表せない、課題に
  収まらない興味・文脈まで扱える。一致根拠は双方の `appeal_summary` で説明し監査性を保つ。
- 類似度を決定論 Python の総当たりにすることで、ベクトルインデックス基盤を持たずに済み、既存の
  「集計は Firestore でなく Python」方針と一貫する。スペース毎に小規模なため O(N) で足りる。
- リリース前のため移行不要（グリーンフィールド）。互換性制約を負わずに最良の形を選べる。

**結果 / 将来課題**:
- グリーンフィールド: 既存 Firestore データは破棄してよい。移行スクリプトは作らない。
- 第1バッチ（本 ADR）は **docs 3 点（本 ADR / SEMANTIC_LAYER 新設 / PHILOSOPHY 原則1 改訂）＋ YAML 概念モデル**
  までを確定し、レビューゲートを置く。
- 第2バッチで `ontology.py`・新規 `semantic_search.py`（埋め込み・総当たりコサイン・appeal 要約生成）・
  `ontology_mapper.py`・`data_integration_agent.py`・`marketing_agent.py`・`segmentation.py`・
  `routers/data.py`・フロント型を YAML に合わせて再実装する。
  - 第2バッチのうち**取り込み層**（ファイル→オントロジー分解、Event-Centric な経路キーの撤去、
    `suggest-event`/`file_event_map` の置換）の概念設計は [`INGESTION_MAPPING.md`](INGESTION_MAPPING.md)
    に切り出し、実装前のレビューゲートとする（マッピング方式: 自動検出＋チャットヒント）。
- 任意で YAML と Pydantic のドリフト検出 pytest（dev-only で PyYAML を導入）を追加する。
- 費用明細・アンケート自由記述など、当面 metrics に畳んだ要素は需要が出た時点で fact dataset 化する。
- **[2026-07 追記] 費用明細を `cost_items` fact dataset へ昇格**: 判断6で `events` の
  metrics に畳んでいた `CostItem`（集計）を、上記の予告どおり独立 fact
  `cost_items`（トップレベル、`event_id` で `events` に紐づく）へ昇格した。
  `events.total_cost` / `cost_per_acquisition` metrics はこの fact の集計を参照する形に
  更新（正典 YAML・`SEMANTIC_LAYER.md`・`ontology.py`・`README.md` 反映済み）。
  アンケート自由記述（`survey_verbatims`）は引き続き未着手。

---

## ADR-009: marketing_agent の自由データ分析を Code Interpreter（Agent Engine サンドボックス）で実現

**ステータス**: 採用

**背景**:
`marketing_agent` の「振り返り・ROI・分布分析」は、定型化しきれない自由なデータ分析を含む。これを
**LLM の応答として数値を出す**（ハルシネーションのリスク）でも、**人手で書いた固定の集計ツール**
（ハードコード／柔軟性の欠如）でもなく、「**AI が分析用 Python を生成 → その実コードが計算 → 生成コードと
結果をチャットに可視化して利用者が検証できる**」という Code Interpreter パターンで実現したい。

当初実装は `BuiltInCodeExecutor`（Gemini サーバー側コード実行）＋ `get_space_data()` がローカル
`/tmp/space_data` に書く Parquet という構成だったが、両者は**ファイルシステムを共有せず**生成コードが
データを読めなかった。加えて Gemini 組み込み code_execution はカスタム関数ツールと併用不可
（ADK 公式: "Single tool per agent"）で、`get_space_data` 等の関数ツールと共存できなかった。
暫定の `UnsafeLocalCodeExecutor`（バックエンドプロセス内実行）は動くが、LLM 生成コードを
サンドボックスなしで実行するため、Cloud Run コンテナのメタデータサーバ経由で GCP 認証情報・他テナント
データに到達しうる。マルチテナント SaaS（ADR-002 の方向）では受け入れられない。

**決定**:
コード実行基盤を **Agent Platform / Agent Runtime の Code Execution Sandbox**（Google 管理の隔離環境）に置く。
当初候補だった Vertex AI Extensions（`VertexAiCodeExecutor`）は **2026-05-26 廃止・2026-11-26 停止**のため不採用。
サンドボックスの呼び出しは ADK の `code_executor`（CodeAct: モデルに ```python フェンスを書かせ ADK が抽出実行）
ではなく、**`run_python_code(code)` 関数ツール**から `sandboxes.execute_code` を直接叩く方式にする。

主要な設計判断:
1. **デプロイは Cloud Run のまま、Agent Engine はマネージドサービスとして外部利用**。バックエンドは
   多機能 FastAPI Web アプリ（ADR-002）であり、エージェント専用ランタイムである Agent Engine には載せない。
   公式に「コード実行サンドボックスはエージェントを Agent Runtime にデプロイしなくても利用可」と明記。
   **Agent Engine（ReasoningEngine）インスタンスを 1 つ作成**し、①コード実行サンドボックスの親、
   ②セッションストア、の 2 役を兼ねる（コードはデプロイしない）。
2. **セッションは `InMemorySessionService` → `VertexAiSessionService`**。サンドボックスは
   `tool_context.state['sandbox_name']` で参照を保持するため、Cloud Run のオートスケール/再起動を跨いで
   消えないようマネージドセッションに永続させる。これによりステートフルなコード実行が本番でも機能する。
3. **コード実行は `code_executor` ではなく `run_python_code` 関数ツール**（Google 移行ガイド Option 3 相当）。
   理由は ADR 末尾「理由」を参照。サンドボックスは `get_space_data()` が `_ensure_sandbox()` でセッション毎に
   作成し state に保存、`run_python_code` が再利用する（変数・ファイルが持続するステートフル実行）。
4. **テナント隔離は構造的に成立**。ADK セッションは `user_id="{space_id}:{uid}"` で名前空間化済みで、
   サンドボックスはセッション毎に作られるため、状態がテナント間で混線しない。
5. **データ受け渡しは CSV を sandbox に直接投入**。`get_space_data()` が各 dataset を CSV(生 bytes)化し、
   `sandboxes.execute_code` の `files` 引数で `persons.csv` 等として投入する。ファイルは file state として
   後続の `run_python_code` でも残る（実機検証済み）。`appeal_vector`（埋め込み）は CSV で扱いづらく分析に
   不要なので除外し、類似度は決定論 Python（`semantic_search.py`、ADR-008 判断5）が担う。
6. **コードと実行結果をチャットに可視化**。`chat_stream` が `run_python_code` の tool_call/tool_response を
   `code` / `code_result` イベントへ変換してフロントの「AIが実行したコード」パネルに表示する。
   これが「利用者が分析内容を検証できる」という本パターンの肝。

**理由**:
- 固定ツール化も LLM 直接集計も避けつつ、透明で監査可能な分析（ADR-008 / Auditable AI 思想）を実現する。
- Google 管理サンドボックスは唯一の本物の隔離境界で、コンテナ認証情報・他テナントデータへの到達を防ぐ。
- Cloud Run + Agent Engine サービスの分離は公式推奨パターンで、現構成からの変更が最小。
- ステートフルサンドボックスにより多段分析（分布把握→軸設計）で変数が持続し、再ロード不要で自然に書ける。
- **`code_executor`（CodeAct）でなく関数ツールにした理由**: 実運用で gemini-3.1-flash-lite がフェンスブロックを
  書かず、存在しないコード実行ツールを呼ぼうとして失敗した（モデルの自然な本能は「コード実行＝ツール呼び出し」）。
  関数ツールはこの本能に沿い、プロンプトでフェンス記法を矯正する必要がない。さらに ADK 2.2.0 と
  aiplatform 1.158.0 の間にあった input_files のキー/型不一致（`contents`↔`content`・base64↔bytes）への
  プロキシシムも、自分で `execute_code` を正しいキー(`content`)＋生 bytes で呼ぶことで不要になり、配線が単純化した。

**結果 / 将来課題**:
- Agent Platform API 有効化、実行 SA に `roles/aiplatform.user`、`scripts/provision_agent_engine.py` で
  Engine を 1 度作成し `.env`（`AGENT_ENGINE_*`）に設定する運用が必要。リージョンは us-central1 等
  （Gemini 呼び出しの global とは分離）。
- 課金: Code Execution / Sessions は 2026-01-28 から従量課金（無料枠あり）。
- サンドボックスは 14 日無使用で状態消失。失効時は `run_python_code` がエラーを返し、`get_space_data()` の
  再実行（サンドボックス再作成＋CSV 再投入）を促す。
- チャート（matplotlib 画像）を返す場合は出力ファイルの受け取り処理を足す（当面は数値分析中心で未対応）。

**捨てた方式（時系列・再評価しないための索引）**:
採用に至るまでに次を順に試して捨てた。詳細は上記「背景」「決定」「理由」を参照。
1. `BuiltInCodeExecutor` ＋ `/tmp` Parquet → FS 非共有で生成コードがデータを読めず、関数ツールと併用不可。
2. `UnsafeLocalCodeExecutor`（プロセス内実行）→ 動くがサンドボックスなしで認証情報・他テナントに到達しうる。
3. `VertexAiCodeExecutor`（Vertex AI Extensions）→ 2026-05-26 廃止・2026-11-26 停止で死に基盤。
4. ADK `AgentEngineSandboxCodeExecutor`（CodeAct）→ モデルがフェンスを書かず失敗＋SDK 不整合をシムで矯正、過剰に複雑。
   → **採用**: `run_python_code` 関数ツールで `sandboxes.execute_code` を直叩き。

**横展開できる学び（他の設計判断にも効く）**:
- **クラウド/エージェント仕様は記憶でなく最新ドキュメントで裏取りしてから計画する**。変化が速く、学習データ
  時点の常識が陳腐化・非推奨化している（上記3が実例。Extensions 廃止を事前検知できた）。
- **フレームワークの暗黙抽象より、明示的でシンプルな実装を優先する**。暗黙機構（CodeAct）は想定外挙動や
  バージョン非互換に当たると矯正コード/プロンプトを生みやすい。自分で呼ぶ方が挙動が読めデバッグも容易（上記4→採用）。
- **本構成は GCP 公式のデカップリング・パターンに合致**（2026-06 ドキュメント検証で追認）。計算は Cloud Run、
  Agent Engine は受動的な状態/サンドボックス基盤として外部利用。Agent Engine は公開 REST を持たないため
  front door を Cloud Run に置く現構成が正しい。**全面移行（計算を Agent Engine Runtime に載せる）は不要**で、
  再評価トリガーは ①Cloud Run のオートスケール限界 ②最大 7 日の長時間ジョブ（`run_query_job`、2026-04-22
  以降作成のエージェント）必須化 ③可観測性/運用負荷の削減効果が改修コストを上回るとき。

---

## ADR-010: ドキュメント⇔実装の乖離是正 / 意味検索の消費側を配線 / 死蔵 API 削除

**ステータス**: 採用

**背景**:
PR#11/#12（ADR-008 OSI 移行 ＋ ADR-009 Code Interpreter）は、データモデル全面再設計・フロント刷新・
エージェント方式変更を一度に含む巨大変更だった。事後監査で、ドキュメントと実装に体系的な乖離が見つかった:
- **思想が WRITE 側だけ実装されていた**: `appeal_summary`/`appeal_vector` は取り込み時に生成・保存される
  のに、消費側（コサイン近接の引き当て）が未配線で `find_similar` はデッドコード。分類は撤回したはずの
  固定ラベル `extracted_challenge` が主信号のままだった。
- **プロンプトが旧モデルで凍結**: `marketing_agent._SYSTEM_PROMPT` が撤回済みの「Event-Centric 原則」を
  宣言し、OSI／意味的近接に触れていなかった。
- **PR 説明と実装の不一致**: 「削除した」とされた死蔵エンドポイント（events 7 本 / integration 3 本 /
  segments router）が全て現存し、フロントから未使用のまま登録され続けていた。
- **ドキュメントが PR に未追従**: SOFTWARE/INFRA は旧アーキ（ingestion/execution_agent・Gemini 2.x・
  旧 API）のまま。PHILOSOPHY は廃止 8 ツールを現役列挙。`/api/data/*` がどこにも未記載。

**決定**:
「実装で得た学びは実装を正、未到達の思想は実装を思想に追いつかせ、撤回した旧概念は実装からも退役」
という原則で一件ずつ整合させた。
1. **意味検索の消費側を配線**: `find_relevant_for_person` ツールを新設（appeal_vector のコサイン近接）。
   `segmentation` の主信号を `appeal_summary` ＋「バケット代表ベクトルとの近接（`find_similar`）」へ移し、
   `extracted_challenge` を主信号から退役（フィールド自体は後方互換で残置）。同期埋め込み
   `embed_text_sync` を追加。
2. **プロンプト凍結解除**: `_SYSTEM_PROMPT` から Event-Centric を削除し、星座型・appeal_summary/
   appeal_vector・Semantic Affinity・意味検索ツールを記述。
3. **成果物の汎用化**: `DeliverablePattern` / `MarketingRun` を Pydantic 化。pattern_id 規約を
   `{bucket}__{format}` に統一し（EMAIL ハードコードと組み立て時フォールバックを撤去）、format を
   データ駆動に。
4. **死蔵 API 削除**: events 7 本（detail/kpi/survey/costs/summary/update/delete）・integration 3 本
   （batch list/report/contacts）・segments router を削除し `main.py` を整理。閲覧は `data.router` に一本化。
5. **Explorer 契約修正**: `/api/data/collections` の件数表示をフロントから外し、lineage は backend が返す
   単数 `job` にフロントを合わせた。
6. **来歴の単純化**: 未 populate の `source_file_id` を退役し、ジョブ単位（`source_job_id` ＋ `filenames`）
   に一本化。
7. **ドキュメント全面同期**: SOFTWARE_ARCHITECTURE 全面改訂、INFRA（モデル名・Agent Engine・リージョン
   2 系統・`aiplatform` 禁止ルール撤回）、PHILOSOPHY（ツール一覧）、SEMANTIC_LAYER / 正典 YAML
   （命名・costs/reports・created_entities・ContentType の欠落値追加）、README/PM/INGESTION を更新。

**理由**:
- 巨大 PR では実装が先行し、思想（doc）が「宣言したが未配線」のまま残りやすい。放置すると次の実装が
  撤回済み概念（extracted_challenge）の上に積み上がり乖離が固定化する。早期の棚卸しで真実源を一致させる。
- 死蔵 API は攻撃面・認知負荷・「使われている」という誤認の温床。PR 説明との不一致は監査性を損なう。

**結果 / 将来課題**:
- セグメント分類は埋め込み I/O が増える（バケット代表ベクトルの生成）。スペースは小規模前提で総当たり
  コサインのコストは許容。大規模化したら近似最近傍や事前計算へ。
- マルチフォーマット（TALK_SCRIPT/PROPOSAL）は配線済みだがプロンプトテンプレは EMAIL 主体。各 format 専用の
  生成プロンプト精緻化は将来課題。
- segments router 削除でパターン/スナップショットの REST 介入窓口は無くなった（HIL はチャット内で実施）。
  人間が成果物を直接編集する UI が必要になれば data.router 側に読み取りを足して再設計する。

**横展開できる学び**:
- **「ドキュメントに書いた＝実装した」ではない**。思想ドキュメントは WRITE/READ 双方の配線が揃って初めて
  「実装済み」。生成だけして消費しない派生データ（ベクトル等）はデッドコード化しやすい。
- **大型 PR の後は doc⇔impl 監査を 1 工程として設ける**。コミットメッセージの「削除した」は実態と乖離し得る。

---

## ADR-011: 取り込みの依存順序化（観測→確定→結合→導出）と同一性の実在照合化

**ステータス**: 採用（概念設計フェーズ。実装は本 ADR と [INGESTION_MAPPING.md](INGESTION_MAPPING.md) のレビュー後）

**背景**:
取り込み後の JOIN が成立しない不具合（`event_attendances.event_id` がどの `events` にも一致しない、`contents.linked_event_id` が常に空）を調査した結果、症状の奥に**取り込みの概念モデルの誤り**があった。

1. **参照の向きと発見の向きの混同**: `event_attendance → person/event` という FK は「マスタが先」に見えるが、この業務で **Person マスタは入力として与えられない**。与えられるのは「イベントで会った記録（参加者行）」で、Person はその観測を重複排除して導出する**派生ディメンション**（DWH の late-arriving / inferred dimension）。参照の向き（FK）と生成・発見の向き（マスタは観測から conform される）が逆であることが、「データ上は person が先・実務では接客が先」という矛盾の正体。
2. **粒度の取り違え**: 参加者リスト1行の自然な粒度は person ではなく「接客（encounter）＝観測」。現行 `_decompose_person`（行→Person を主役に分解）はこれを person 粒度と混同していた。
3. **順序非依存の演出が脆い**: ADR-008 以降、リンクを `stable_id(名前)` ＋ `_write_link_stubs`（inferred member の仮メンバー生成）で順序非依存に見せていた。しかし自然キー（名前ハッシュ）が表記揺れで割れ、後から実体と突き合わせ統合する工程も無いため分裂する。`_build_content` はそもそもリンク解決を呼ばず contents→event が常に未解決だった。
4. **ファイル到着順 ≠ 依存順** なのに、各ファイルを独立・並列処理しており依存順序を内部に持たない。

**決定**:
ターゲットのスキーマ（OSI 5マスタ＋ファクト、ADR-008）は維持する。**誤っていたのは取り込みの概念モデル**であり、これを以下へ再設計する。詳細な概念設計は [INGESTION_MAPPING.md](INGESTION_MAPPING.md)。

1. **多段パイプライン「観測 → 確定(conform) → 結合(bind) → 導出(derive)」**。`process_batch` をバッチ横断の多段にし、依存順（マスタ確定 → ファクト結合 → person 集約）を内部に持つ。
2. **観測(observation)の明示**: ファイルの各行を、列分解の前に **JSON object の行ブロック（`{元列: 値}`、ロスレス）** として捕捉する概念を導入。これが接客の観測＝ファクトの源泉。**一過性**（永続コレクションは作らず OSI 5マスタにも足さない。取り込みは「プロセス」であり OSI の構成要素ではない＝INGESTION_MAPPING の方針を踏襲）。
3. **全エンティティ UUID 主キー ＋ 検索ベースの find-or-create**: `stable_id`（名前ハッシュ）方式を全廃。重複排除も参照解決も「スペース内の実在エンティティを natural key（events=名前 / accounts=会社名 / products=製品名 / persons=email→氏名×会社）で検索し、ヒットすれば既存 UUID を再利用、無ければ採番」に一本化する。照合は NFKC＋全空白除去＋lower で正規化し、外れたら曖昧一致でフォールバックして根拠を job ログに残す。`_write_link_stubs` は撤去。データ量が小さい前提で各種別をメモリに読み O(N) 照合で足りる。
   - UUID 化の含意: 名前→ID の計算ショートカットが消えるため、参照は必ず確定済みマスタへの検索で解決される。これが多段の依存順を構造的に強制し、`event_id` 分裂・contents→event 未解決・stub 分裂を一掃する。
4. **接客事実を EventAttendance へ**: `owner_staff`（接客担当）/`challenge_note`（課題感）/`memo`（所感・要望・注意）を `event_attendances` に追加。旧 `Person.notes`（行→Person に集約していた接客メモ）は廃止。
5. **Person.appeal_summary はロールアップ導出**: その人の全 `event_attendances`（各回の接客担当・課題感・メモ）＋興味製品を集約して、取り込みの導出フェーズで `appeal_summary` / `appeal_vector` を生成する（ファイル単位生成をやめる）。

**理由**:
- 参照の向きと発見の向きを分離し、観測（staging）と確定（conform）を明示することで、実務の作業順とデータモデルの整合が両立する（矛盾は概念の欠落から来ていた）。
- 「実在マスタへの検索 find-or-create」に一本化すると、person 名寄せ・event/account/product 解決・contents→event を**単一機構**で扱え、stub と安定IDの二重管理・表記揺れ分裂が根絶される。
- UUID 主キーは名前変更に強く、ID 計算の脆さを排す。小規模データなら検索コストは許容（ADR-008 の「集計は Python・総当たり」方針と一貫）。

**結果 / 将来課題**:
- グリーンフィールド方針（ADR-008）に従い、既存 Firestore データは破棄して入れ直す（移行スクリプトは作らない）。
- 観測(observation)は当面**永続しない**。後から名寄せをやり直したい／生データ監査が要る要件が出たら、landing コレクション（例 `source_records`）へ昇格する（その時点で再 ADR）。
- 未解決リンク（検索しても該当マスタが無い）時の挙動（スキップ／保留／ユーザー確認）は INGESTION_MAPPING で確定する。
- 本 ADR と INGESTION_MAPPING の改稿を**レビューゲート**とし、承認後に実装（`data_integration_agent`／`ontology_mapper`／`semantic_search`／`routers/integration`＋フロント／`segmentation`）へ進む。

**横展開できる学び**:
- **症状（FKが入らない）の修正前に、参照の向きと生成・発見の向きを分けて捉える**。マスタが観測から導出される（inferred dimension）ドメインでは、FK の向きと取り込みの順序は逆になり得る。
- **「順序非依存に見せる」設計（安定ID＋stub）は、自然キーの正規化が完全でない限り破綻する**。小規模なら素直に依存順で多段処理し、実在への検索で解決する方が堅い。

---

## ADR-012: デプロイの IaC 化 — GCP は Terraform、Firebase は CLI / App Hosting に責務分割

**ステータス**: 採用

**背景**:
これまでインフラは完全に手作業で構成されていた（`.tf` なし・CI/CD なし・`provision_agent_engine.py` のみ）。既存の `marketing-mail-generator` 1プロジェクトには Firestore `(default)`・Auth(Google Sign-In)・Web アプリ・Agent Engine が手作業で既に存在する。これを本番として実デプロイするにあたり、再現性とドリフト検知のため GCP 側を IaC 化したい。一方 Firebase 側に独自の構成管理ツールを足すのは過剰になりうる。

**決定**:
1. **環境は既存1プロジェクトを Terraform に import して正典化**（dev/prod 分割はしない。`var.project_id` で将来拡張可能な構造のみ用意）。`infra/terraform/` に配置、state は GCS リモート。
2. **責務を手段で分ける**:
   - **Terraform** = GCP インフラの「箱」（API 有効化・Artifact Registry・Cloud Run・SA/IAM・Firestore DB 本体・Storage バケット+ライフサイクル・Firebase プロジェクト/Web アプリ・App Hosting backend・GitHub Actions 用 WIF）。
   - **Firebase CLI**（`firebase deploy`）= アプリ成果物の Firestore **ルール/インデックス**・Storage **ルール**。
   - **App Hosting**（git push 自動）= フロント（Next.js SSR）のビルド&デプロイ。
   - **GitHub Actions**（WIF キーレス）= バックエンド（Cloud Run）のビルド&デプロイ。
3. **フロント配信は Firebase App Hosting に統一**。旧 `firebase.json` の Web Frameworks 設定（`hosting.source` + `frameworksBackend`）は撤去し、`frontend/apphosting.yaml` で構成する。
4. **Agent Engine は Terraform 管理しない**。`google_vertex_ai_reasoning_engine` は `spec.package_spec`（デプロイ済み ADK コードの GCS 成果物）を前提とするリソースで、当プロジェクトの「コードレスのマネージドランタイム（サンドボックス＋セッションストア）」という使い方と一致せず、import すると恒常的な差分になる。よって作成は従来どおり `provision_agent_engine.py` に委ね、出力（resource name / id）を Terraform 変数として Cloud Run の env に注入する。
5. **Auth は Firebase 管理のまま**。Google Sign-In の OAuth クライアントは Firebase が自動管理しており、Identity Platform リソースとして Terraform 管理すると client secret の二重管理や競合が生じる。利点に対しコストが高いため対象外（将来 Identity Platform へ寄せる場合の雛形は `auth.tf` にコメントで残す）。
6. **Secret Manager は使わず Cloud Run の平文 env**。`config.py` が読む値（プロジェクト ID・ロケーション・Agent Engine ID・配信オリジン）はいずれも秘匿情報ではない。`docs/INFRA_ARCHITECTURE.md` の旧「Secret Manager 管理環境変数」表からは意図的に簡素化する。SA には `secretmanager.secretAccessor` を残し、将来の真の秘密が出た時点で `secrets.tf` の雛形を有効化する。

**理由**:
- 「インフラの箱は宣言的に、アプリのルール/コードは各プロダクト純正の配信経路で」分けると、二重管理と過剰抽象を避けつつ手作業を最小化できる。
- ブラウンフィールド import（宣言的 `import {}` ブロック）なら既存稼働を壊さず正典化でき、`terraform plan` の no-op を以後のドリフト検知に使える。
- App Hosting・GitHub Actions(WIF) はいずれも push 起点で人手を介さず、鍵管理も不要（キーレス）。
- プロバイダのリソースが当プロジェクトの使い方と合わない箇所（Agent Engine・Auth）は、無理に IaC 化せず最小の手作業＋スクリプトに留める方が堅い。

**結果 / 将来課題**:
- 一度きりの手作業として残るのは: ① tfstate バケット作成、② App Hosting の GitHub OAuth 連携（Developer Connect）、③ Blaze 課金紐付け、④（未初期化時の）Auth 有効化。それ以外は `terraform apply` で自動化。
- App Hosting backend は GitHub 連携（`app_hosting_repository`）が空の間 `count=0` で apply をブロックしない。連携後に値を入れて再 apply。
- dev/staging 環境が必要になったら別プロジェクト＋`var.project_id` で複製する（現状は単一環境）。
- 検証は `terraform plan` の no-op、`/health` 200、App Hosting 自動ビルド、`firebase deploy --only firestore,storage`、アプリ E2E（スペース作成→取り込み→チャット）で行う。

**横展開できる学び**:
- **IaC は「全部 Terraform」ではなく責務で割る**。ルールやフロントは各プロダクト純正の配信に任せた方が単純で壊れにくい。
- **プロバイダにリソースがあっても、それが自分の使い方をモデル化しているとは限らない**（Agent Engine の `package_spec` 問題）。スキーマ不一致は import 前に気づき、変数注入などの逃げ道に切り替える。
- **既存手作業環境は greenfield で作り直すより import で正典化**する方が、稼働中の Auth/API キー/データを失わずに済む。

---

## ADR-013: 取り込みパイプライン再設計 — AI が直接抽出・業務判定を排除

**ステータス**: 採用

**背景**:
ADR-011 で設計した取り込みパイプラインには以下の問題があった:
- AI 思考フェーズ（SchemaMapper）が浅い: ヘッダー+5行サンプルのみで業務文脈を把握できない
- `EngagementLevel`（アポ獲得済み/感度高/通常リード）を取り込み時に自動分類していた
- 「決定論 Python」という概念が実装の役割を曖昧にし、複雑さを生んでいた

**決定**:
1. **取り込み時の業務的判定を廃止**。`EngagementLevel` 分類（`_classify_engagement`）を削除。
   感度・興味度の「観測事実」（「感度A」等のテキスト）は `event_attendances.challenge_note` にそのまま保存し、ベクトル検索に乗せる。
2. **AI Extract を2段構えに再設計**:
   - **Step 1（バッチ横断1回、フルモデル）**: `understand_batch()` — バッチ内全ファイルのヘッダー+サンプル+オントロジー定義を渡し、各ファイルの `DocumentPlan`（業務文脈・エンティティ種別・カラムマッピング・リンクヒント）を生成する
   - **Step 3（CSVのみ、行単位並列、軽量モデル）**: `_extract_rows_parallel()` — DocumentPlan を文脈として各行を `asyncio.gather` で並列抽出する
3. **役割分担の明確化**:
   - **AI**: ファイルの業務文脈を読み解き、観測事実を構造化データとして返す（コード生成しない）
   - **Python**: Firestore 読み書き・UUID 採番・find-or-create（業務判定はしない）
4. **`OntologyMapper` の CSV パス（`map_rows`）を廃止**。TXT パス（`map_extraction`）のみ残す。

**理由**:
- `appeal_vector` によるコサイン類似度で動的判断できるため、取り込み時の感度分類が不要になった
- 「感度A」等の観測事実は `challenge_note` → `appeal_summary` → `appeal_vector` の経路でベクトル検索に活きる
- 全行一括送信はトークン消費が大きく非効率。行単位並列なら1行=数十トークンで軽量モデルが使える
- バッチ横断理解（Step1）により、参加者 CSV と同バッチのイベント概要 TXT を横断して「このCSVはこのイベントの参加者」と正しく判断できる

**結果**:
- `EngagementLevel` 型・`Person.engagement_level` フィールドを削除
- `DocumentPlan` モデルを追加（`ontology.py`）
- `IntegrationJob.column_mapping` を `DocumentPlan | None` に更新
- `OntologyMapper._classify_engagement` / `map_rows` / `_decompose_person` を削除

---

## ADR-014: プロダクト名を EventTune に確定 — GCP プロジェクト ID は維持し個別リソースのみ改称

**ステータス**: 採用

**背景**:
[NAMING_PROPOSAL.md](2026-07-02_レビュー/NAMING_PROPOSAL.md) §7 でプロダクト名は一度
「EventWeave」に確定していたが、商標・ドメイン調査および Firebase/GCP プロジェクト ID の
重複可能性を踏まえた最終検討の結果、**EventTune** に変更された（詳細は
[MESSAGING_EVENTTUNE.md](2026-07-02_レビュー/MESSAGING_EVENTTUNE.md)）。旧名
`marketing-mail-generator` は命名規約（本書 §6 `generator` 禁止）に違反しており、確定した
新名称への反映が必要だった。

当初は新規 GCP/Firebase プロジェクト（`eventtune`）を作成し、旧プロジェクトから完全移行する
方針を検討したが、**GCP 側のプロジェクト作成クォータ制限に抵触し新規プロジェクトを作成できな
かった**。

**決定**:
1. **GCP/Firebase プロジェクト ID `marketing-mail-generator` は維持する**。ユーザーからは
   見えない内部識別子であり、ADR-012 で確立した Terraform カバレッジ（Cloud Run・Artifact
   Registry・SA/IAM・WIF・Firestore・Firebase Web App・App Hosting backend が全て state 管理下）
   により、プロジェクトを跨がなくても個別リソースの改称は `terraform apply` 一発で安全に行える。
2. **Terraform 管理下の個別リソースのみ `eventtune-*` に作り直す**（Cloud Run `mmg-api` →
   `eventtune-api`、Artifact Registry `mmg` → `eventtune`、サービスアカウント `mmg-api-sa` →
   `eventtune-api-sa`、App Hosting backend `mmg-frontend` → `eventtune-frontend`）。
   リソース属性値（`name`/`account_id`/`repository_id`/`backend_id`）のみ変更し、Terraform
   リソースアドレス（ラベル名）は state 移行の複雑化を避けるため変更しない。
3. **GitHub リポジトリ名も `eventtune` に改称**する（`gh repo rename`、旧 URL は自動リダイレクト）。
4. **カスタムドメイン `app.eventtune.link`（AWS Route 53 で取得）を App Hosting に割り当てる**。
   プロジェクト ID を維持する以上、App Hosting の既定配信 URL
   （`https://<backend_id>--marketing-mail-generator.<region>.hosted.app`）と Firebase Auth の
   `authDomain`（`marketing-mail-generator.firebaseapp.com`）には引き続きプロジェクト ID が
   残るため、ユーザーに見える面（既定配信 URL）はカスタムドメインで隠す。authDomain 自体は
   ログインポップアップの一瞬の遷移にしか出ないため据え置く。トップレベル `eventtune.link` は
   将来のランディングページ用に予約し、今回は割り当てない。

**理由**:
- 本番データがまだ無いため、Terraform 管理下リソースの destroy→create は許容できる
  （ADR-011/ADR-008 のグリーンフィールド方針と整合）。
- ADR-012 の IaC 化により、直近デバッグした App Hosting ビルド用 IAM 権限
  （`developerconnect.user` 等）は既に `service_account.tf` に定義済みであり、同一プロジェクト内
  でのリソース作り直しでも再取得され、デバッグをやり直す必要はない。
- GCP プロジェクト ID はサービス提供上ユーザーに直接見せる情報ではなく、カスタムドメインで
  代替可能なため、クォータ制限という制約下でも「見た目」上の目的は達成できる。

**結果 / 将来課題**:
- `docker タグ・Artifact Registry の中身（旧イメージ）は引き継がれない。次回 CI push で
  新規リポジトリに再構築される。
- App Hosting backend の改称で既定配信 URL が変わるため、`frontend_origin`（CORS）を
  `https://app.eventtune.link` に更新し再 apply する必要がある。
- 商標調査は「EventWeave」時点のものであり、「EventTune」については未実施。対外公開前に
  別途実施する。

**横展開できる学び**:
- **プロダクト名とインフラの内部 ID は独立して扱える**。GCP/Firebase プロジェクト ID は
  作成後不変だが、ユーザー向けの見た目はカスタムドメインで完全に切り離せるため、
  プロジェクト ID の改称可否がプロダクトのリブランディングを妨げる理由にはならない。
- **IaC のカバレッジが高いほど「作り直し」のコストは下がる**。ADR-012 で個別リソースを
  Terraform 管理下に置いていたことが、今回のクォータ制約への迅速な方針転換を可能にした。

---

## ADR-015: 取り込み再建 — スペック駆動の統一パイプラインと確認済みバッチ文脈

**ステータス**: 採用（実装済み — 2026-07。概念設計の正典は [INGESTION_MAPPING.md](INGESTION_MAPPING.md)）

**背景**:
取り込み層（DataIntegrationAgent）は ADR-008（Event-Centric 撤回）→ ADR-011（依存順の多段化・UUID 化）→ ADR-013（行単位 AI 抽出・業務判定排除）と3世代の再設計を経たが、各世代の考え方を統一する背骨がないまま産物が同居しており、処理方式がチグハグになっている。コード監査で確認した具体的問題:

1. **承認したプランと実行されるプランが別物**。プラン提案 `POST /api/integration/plan` は `understand_batch()` の結果を画面に返すだけで破棄し（`routers/integration.py:76-116`）、実行 `POST /api/integration/batches` → `process_batch()` が**別の LLM 呼び出しで理解をやり直す**（`data_integration_agent.py:1142`）。LLM 出力は毎回揺れるため、ユーザーが確認した内容と異なるマッピングで取り込まれることが構造的に起こり得る。
2. **サイレント欠損**。イベントリンクが解決できない参加観測は `_bind_facts` がファクトを作らずスキップする（`data_integration_agent.py:935-940`）。この欠落は `skipped_records` にすら記録されず（そこに載るのは AI 抽出段のスキップのみ）、唯一の痕跡はサーバーログである。ユーザーからは「取り込んだのにデータが無い」ようにしか見えない。
3. **明示イベント指定が死んでいる**。API にはバッチの既定イベントを渡す `event` パラメータがある（`integration.py:183`。リンク解決の第2優先シグナル）のに、フロントは送っていない（`dashboard/page.tsx:290-294` は files/hint のみ）。実運用は「バッチ内でイベントがちょうど1つ確定したら、リンク未指定の観測をそのイベントへ束ねる」単一イベントフォールバック（`data_integration_agent.py:897-900`）という**暗黙のヒューリスティクス**頼みで、外れると 2. の黙殺に落ちる。
4. **データセット追加のコストが高い**。種別を1つ足すには、①プロンプト内のオントロジー定義（`_ONTOLOGY_DEFINITION` / `_BATCH_UNDERSTAND_PROMPT` / `_DOCUMENT_EXTRACTOR_PROMPT` の3箇所に手書き重複）、②抽出用 Pydantic スキーマ群、③`ontology_mapper.py` の種別別ビルダー（日本語 enum 対応表をハードコード）、④確定/結合ステージの分岐、を**バラバラに編集**する必要がある。費用 CSV だけ「AI が column_map を決め、Python が全行に適用する」別方式（`_extract_cost_rows_from_csv`）という非対称もある。「新しいデータが増えるたびに取り込みロジックが増えて管理しきれない」という懸念はこの構造から来ている。
5. **ドリフト残骸**。削除済み `SchemaMapper` への言及（`data_integration_agent.py:9`）、ADR-010 で削除したはずの `/report` `/contacts` を宣伝し続ける docstring（`integration.py:11-12`）、`README.md:46` の `run_schema_mapper`、死蔵 `ColumnMappingResult`（`ontology.py:356`）、ADR-013 で削除した `EngagementLevel` の残存（`osi_event_marketing_v1.yml:69` / `marketing_agent.py:115,117,179` / `segmentation.py:8,192` / PHILOSOPHY_AND_NAMING 各所）。[CURRENT_ISSUES.md](2026-07-02_レビュー/CURRENT_ISSUES.md) に監査済み。
6. **潜在バグ・運用**。PDF は UI が受け付ける（`dashboard/page.tsx:752` の accept リスト）のに、`_read_text` は UTF-8 強制デコードの文字化けを AI に渡す（`data_integration_agent.py:523-524`）。取り込みは FastAPI BackgroundTask 実行（`integration.py:217`）のため、Cloud Run の縮退でジョブが `processing` のまま固まり得る（CURRENT_ISSUES P2-2）。

目指す UX は「イベントに関するあらゆるファイルを好きなだけ一度にアップロードすると、AI がオントロジーへマッピングする」こと。これを、LLM の失敗（列対応の幻覚・イベント名の誤読）を**黙って吸収せず可視化**しながら、データ形状が増えても取り込みロジックを増やし続けない構造で実現する。

**決定**:
ADR-011 の依存順の骨格（観測→確定→結合→導出）と ADR-013 の「取り込み時に業務判定をしない」原則は維持する。作り直すのは「ファイルが観測になる過程」と「イベントリンクの決め方」である。グリーンフィールド方針（ADR-008/011）に従い、既存データは破棄して再取り込みする。概念設計の正典は全面改稿した [INGESTION_MAPPING.md](INGESTION_MAPPING.md)。

1. **Python 利用の4形態を定義し、責務を「AI か Python か」ではなく実行形態で割る**。「決定論 Python」という用語は廃止する（ADR-013 自身が「実装の役割を曖昧にし複雑さを生んでいた」と記録済み）。

   | 形態 | 内容 | 本リポジトリの既存例 |
   |---|---|---|
   | **P1: 基盤コード** | 人間が書き・レビュー・テストする恒久ロジック。挙動はコードで固定 | パイプライン各ステージ、Firestore 読み書き |
   | **P2: ツール関数** | 用意済みの関数を AI エージェントが会話中に選んで呼ぶ。制御フローは AI、副作用は関数の契約内 | marketing_agent のツール群 |
   | **P3: AI 生成仕様の機械適用** | AI が**コードではなく宣言的な変換仕様（データ）**を生成し、P1 がそれを解釈・適用する。AI の判断が成果物として残り、承認・監査・再実行できる | 費用 CSV の取り込み（column_map 方式） |
   | **P4: AI 生成コードのサンドボックス実行** | AI が Python コードを書き隔離環境で実行。表現力最大、検証コスト最大 | marketing_agent の自由分析（ADR-009） |

   取り込みでの配置: **骨格（ステージ順序・永続化・採番・名寄せ・テナント境界）= P1** / **ファイル→観測の変換 = P3 を既定** / 仕様で表現できない列のみ、仕様内で宣言された範囲の行単位 AI 抽出（`ai_parse`）/ **P4 は v1 不採用**（拡張条件は将来課題に記す）/ **P2 は取り込み後の運用操作**（保留の再バインド・レポート説明）に使い、バッチ実行の制御フローには使わない。
2. **IngestionSpec レジストリを新設し、データセット追加を「モデル定義＋レジストリ1エントリ」に集約する**。`routers/data.py` の `VIEWS`（種別追加=1行）と同じ経済性を取り込みに適用する。スペックは 種別・役割（master/fact/patch）・対応 Pydantic モデル・自然キー・リンク定義・抽出スキーマ・業務的意味の一段落・正規化関数・appeal 要否 を持ち、そこから **プロンプトのオントロジー定義（3箇所の手書き→1レンダラー）・抽出スキーマ整合チェック（ドリフトをテスト失敗に変える）・汎用ビルダー（種別別ビルダーと enum ハードコードの置換）・確定/結合の依存順ループ** を導出する。手書きが残るのは業務的意味の散文・抽出スキーマ宣言・特殊な正規化関数のみ。
3. **表形式/文書のパスを統一し、変換は P3 を既定にする（ADR-013 決定2の改訂）**。CSV か文書かの違いは Read ステージ（行ブロックか文書ブロックか）に閉じ込め、以降は同一の流れにする。行単位 AI 抽出（ADR-013 Step 3）は既定から `ai_parse` 列限定の opt-in に降格し、表形式の既定は「AI が仕様を1回生成 → 人間が確認 → P1 が全行に適用」とする（費用 CSV 方式の原則昇格）。あわせて「1ファイル=1 entity_type」の制約を撤廃し、`FilePlan.targets` を複数化する。
4. **取り込みプランを契約にする**。プラン提案が返した `BatchPlan`（per-file の `FilePlan` ＋ バッチ既定イベント提案）を確認 UI でユーザーが承認し、**承認済み BatchPlan をバッチ実行にそのまま再提出して実行する**。実行側での `understand_batch()` 再実行を廃止し、「承認したものが実行される」ことを構造で保証する。
5. **イベントリンク解決を「行の列値 → 確認済みバッチ既定イベント → 保留」に改める**。理解ステージが「このバッチは『○○展示会』の関連データ」という既定イベントを**根拠付きで提案**し、ユーザーが確認/変更/「イベントなし」を選ぶ。単一イベントフォールバックとサイレントスキップは廃止し、それでも解決できない観測は source_records 上で `pending`（理由付き）となり、ファクトは書かず、バッチ報告に必ず現れる（person 自体は作る=現行踏襲）。死んでいた `event` パラメータは「確認済み既定イベント」として UI が実際に送る値になる。`hint` はリンク決定の機構から降格し、理解ステージへの曖昧解消の補助入力として存続する。
6. **source_records（着地ゾーン）を新設する（ADR-011 保留事項の再決定）**。Read ステージが全観測ブロックを `{元列: 値}` のまま永続する（batch_id / filename / row_no / status ∈ bound|pending|skipped / 理由 / 生成ファクトへの参照）。ADR-011 は「要件が出たら landing コレクションへ昇格（その時点で再 ADR）」としており、①保留観測の置き場、②オントロジー成長時の再処理（生データ＋承認済み変換仕様の組で再実行）、③取り込み根拠・インジェクション事後調査、の3要件が成立したため昇格する。取り込みは「プロセス」であり OSI の構成要素ではないという境界は維持する（正典 YAML・SEMANTIC_LAYER には足さない。Explorer の `VIEWS` には可視化のため1行足す）。元ファイルのバイト列は保管しない。
7. **運用堅牢性とインジェクション姿勢を同梱する**。①ジョブはステージ毎にハートビートを書き、一定時間更新のない `processing` を error に倒す掃除処理を持つ（Cloud Tasks 化は ADR-002 のトリガー発火まで保留のまま）。②PDF は accept リストから外し API でも 4xx で拒否する（multimodal 読み取りは Read ステージの拡張点として将来課題に記す）。③バッチ報告は P1 が集計した事実（作成/更新件数・保留と理由・新規採番マスタ・曖昧一致の解決根拠）を AI が Markdown に整形してチャットへ出す（専用 UI は作らない）。④アップロード内容はデータとして扱い指示と区切る・AI 出力はスキーマ限定・細工 CSV による定期評価（カナリア）を実装フェーズの検証に含める（CURRENT_ISSUES P2-1 への応答）。

**検討した選択肢（イベント紐付け）**:

| 案 | 内容 | 評価 |
|---|---|---|
| A: 現状維持 | 自由バッチ＋自動解決＋単一イベントフォールバック＋未解決スキップ | 不採用。スキップが不可視（サイレント欠損）。フォールバックは「たまたま1イベントだけ確定した」ときに誤束縛し得る。AI がイベント名を誤読すると find-or-create が幽霊マスタを作る |
| B: 1アップロード=1イベント強制 | アップロード時にイベント選択/新規作成を必須化。行内の列で上書き | 不採用。製品マスタ・コンテンツ一覧・年間計画書（複数イベント）など**イベントに属さない/複数イベントのファイルが成立せず**、「イベントなし」例外が必ず要り結局 D に近づく。「ファイル→イベントの事前割当」は ADR-008 が撤回した経路キーの再導入でもある |
| C: 完全 HIL（事後解決） | 自由バッチのまま、未解決は全て取り込み後の保留キューでユーザーが解決 | 部分採用。欠損は消えるが、放置されると保留の墓場になる。保留ストア＋再結合＋解決 UI が初期実装の必須要件になり重い |
| **D: 確認済みバッチ既定イベント＋保留（採用）** | 確認ステージで AI が既定イベントを提案し、ユーザーが承認/変更/「なし」を選ぶ。行の列値が常に優先。残った未解決だけ保留にして報告 | 摩擦は既存の確認ステップに1項目増えるだけ。欠損は起こらず、起きたことは全て見える。C の保留機構は最小構成（一覧＋チャット/API 再バインド）だけ取り込む |

**なぜ D は Event-Centric への回帰ではないか**: ADR-008 が撤回したのは「`event_id` を取り込み処理全体の**経路キー**にする」設計（ファイルをイベントへ事前割当し、分解・永続の全経路がそれに依存する）である。D の既定イベントは、ファクトの1つの FK を埋めるための**最下位のシグナル**にすぎない。人間確認済みで、行の列値で常に上書きされ、マスタや非イベント系ファイルには一切関与しない。Event は5マスタの1つのままであり、変わるのは「最後のシグナルが隠れたヒューリスティクスから確認済みの値になる」ことだけである。

**検討した選択肢（Python 利用形態）**:
- **エージェント主導の取り込み（P2 で制御フロー）**: 取り込みエージェントがツール（マスタ登録・ファクト結合等）を会話的に呼び分ける案。柔軟だが、バッチ取り込みの要件は再現性と完全性（全行の行き先が必ず記録される）であり、ステージ飛ばし・順序崩れを構造的に防げないため不採用。チャットは確認と運用操作の面として使う。
- **生成コードによる変換（P4 既定）**: ファイル毎に AI が pandas 変換コードを書きサンドボックスで実行する案。表現力は最大だが、生成コードの正しさ検証・アップロード内容経由のインジェクション表面・オントロジー整合の保証が重く、現対象（表形式＋文書）は宣言的仕様で足りるため不採用。拡張条件は将来課題に記す。

**理由**:
- **承認の対象を「プロセス」から「成果物（仕様）」に変える**ことで、確認 UI が飾りではなく契約になり、問題1が根治する。LLM の判断を1回に集約すると、コストが行数に比例せず、誤りは仕様の1箇所に系統化されて人間の目が届く場所（確認画面・バッチ報告）に必ず現れる。
- **「見えない救済」より「見える保留」**。フォールバックやサイレントスキップは成功時に速いが、失敗時にユーザーの信頼を最も傷つける形（気づけない欠損）で壊れる。保留は欠損を作業可能な状態に変える。
- **レジストリはこのリポジトリで実証済みのパターン**（`VIEWS`）であり、「オントロジー追加時に取り込みロジックが線形に増える」問題への最小の構造的回答。プロンプト・スキーマ・ビルダーをモデル定義から導出することで、ADR-008 が問題視した「同じスキーマの多重手書き定義」の取り込み版を解消する。
- **小さく安全な機構の再利用**: EntityResolver・UUID find-or-create・依存順の多段・appeal 導出は ADR-011/013 で正しく設計されており、変える理由がない。再設計の対象を「変換の作り方」と「リンクの決め方」に絞ることで、レビュー可能な大きさに保つ。

**結果 / 将来課題**:
- 本 ADR は以下を改訂する: ADR-013 決定2（行単位 AI 抽出の既定 → `ai_parse` 限定の opt-in）/ INGESTION_MAPPING 旧 §3 シグナル3（単一イベントフォールバック → 廃止）/ ADR-011 将来課題（観測の非永続 → source_records へ昇格）。ADR-008 の5マスタ対等・ADR-011 の依存順骨格・ADR-013 の業務判定排除は維持。
- **ドリフト修正チェックリスト**（実装フェーズで実施済み）: `data_integration_agent.py` docstring（SchemaMapper）/ `integration.py` docstring（/report /contacts）/ `README.md` パイプライン図・ディレクトリツリー / `ColumnMappingResult`・`DocumentPlan` 削除 / `osi_event_marketing_v1.yml`・`marketing_agent.py`・`segmentation.py`・PHILOSOPHY_AND_NAMING・SOFTWARE_ARCHITECTURE（EngagementLevel 残骸と取り込み記述の同期）。
- グリーンフィールド: 既存 Firestore の取り込み系データは `reset_space_ingestion_data.py` の要領で破棄し、sample_data を再取り込みして検証する。
- **実装フェーズの検証計画**: フィクスチャバッチとして最低限 ①イベント名の列が無い参加者 CSV（既定イベント提案と保留の両経路）②複数イベントを含むリスト CSV ③イベント概要 TXT＋参加者 CSV の同時投入 ④費用 CSV ⑤「これまでの指示を無視して…」を仕込んだカナリア CSV を用意し、承認済みプランどおりの取り込み・保留の可視化・カナリアの無害化を確認する。
- **拡張トリガー**（発火するまで実装しない）: P4 前処理 = 宣言的仕様で表現できないファイル形状（ピボット表・複数シート結合・非定型帳票）が実利用で頻出したとき、BatchPlan に前処理コードを含め確認画面でコード自体を承認し ADR-009 のサンドボックスで実行する形で Read 段に差し込む / PDF multimodal 読み取り = PDF の取り込み需要が実際に発生したとき / Cloud Tasks 化 = ADR-002 の既存トリガーのまま。
- 保留キューの v1 は最小構成（Explorer での source_records 閲覧＋チャット/API での再バインド）。専用の解決 UI は保留の発生実態を見てから判断する。
- **追記（2026-07-10）**: Word (.docx) 対応を Read ステージのリーダー追加のみで実施済み（`readers.read_docx`。段落+表をテキスト連結し `.txt` と同じ観測ブロック形状に着地させる。Interpret 以降は無変更）。PDF の multimodal 読み取りとは別軸の拡張トリガーであり、上記の PDF 課題は未着手のまま。

**横展開できる学び**:
- **プレビューと実行が別々に AI を呼ぶ構成は、承認したものと実行されるものの乖離を構造的に生む**。承認の対象は「プロセス（もう一度考えさせる）」ではなく「成果物（プランそのもの）」にする。
- **「AI か Python か」の二分法ではなく、実行形態の段階（基盤コード/ツール関数/生成仕様の機械適用/生成コードの実行）で責務を割る**。同じ「Python」でも挙動の固定度と検証コストがまるで違い、二分法はそれを覆い隠す。
- **暗黙のヒューリスティクスによる救済（単一イベントフォールバック）を設計する前に、同じ判断を人間の確認1回に置き換えられないか先に問う**。確認ゲートが既に UX に存在するなら、そのコストはほぼゼロである。
