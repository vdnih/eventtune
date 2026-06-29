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
