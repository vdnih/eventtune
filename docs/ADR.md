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
- 任意で YAML と Pydantic のドリフト検出 pytest（dev-only で PyYAML を導入）を追加する。
- 費用明細・アンケート自由記述など、当面 metrics に畳んだ要素は需要が出た時点で fact dataset 化する。
