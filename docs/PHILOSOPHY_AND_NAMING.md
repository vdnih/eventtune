# システム思想と命名規約

## 1. このプラットフォームとは何か

このシステムは **EventTune**（イベントマーケティング・インテリジェンス）である。
プロダクト名の由来・確定経緯は
[NAMING_PROPOSAL.md](2026-07-02_レビュー/NAMING_PROPOSAL.md) と
[MESSAGING_EVENTTUNE.md](2026-07-02_レビュー/MESSAGING_EVENTTUNE.md) を参照。

展示会・セミナー・イベントを中心に、カオスなマーケティングデータをオントロジーに統合し、
AIエージェントがそのオントロジーの上でマーケティング活動を行うプラットフォームを目指す。

**現在の実装範囲**:

- **データ統合**: 展示会リストのCSV/Excel、イベント概要・KPI・費用・アンケートのテキストをアップロードし、
  二段階処理（AI一次処理 → Python加工処理）で OSI セマンティックレイヤー（5 マスタ: persons / accounts /
  events / products / contents と、event_attendances 等のファクト群）に統合する。
  概念モデルの正典は [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md)。来歴（DataLineage）と加工根拠も記録する。
- **マーケティングエージェント**: `MarketingAgent` がチャット（SSE）でユーザーと対話し、ツール群を通じて
  オントロジーを参照しながら、参加者への個別フォローアップメール生成、イベント振り返り分析、
  戦略レポート保存などを汎用的に実行する。

### これはメール生成ツールではない

| よくある誤解 | 正しい理解 |
|---|---|
| テンプレートに変数を埋め込むツール | 相手の状況・課題・温度感をAIが読み取り、ふさわしい構成と言葉を選ぶツール |
| メール生成が目的 | メール生成は最初のユースケース。振り返り分析・戦略提案へ拡張している（一部実装済み） |
| リードリストを処理するツール | イベントを中心軸に、ハウスリスト・KPI・費用・アンケート等を統合するプラットフォーム |

---

## 2. 設計原則

### 原則1: 意味は OSI セマンティックレイヤーに集約する（多マスタ・コンステレーション）

> ⚠️ **改訂（ADR-008）**: 旧「原則1: イベントが中心概念（Event-Centric）」は撤回した。
> Event は唯一のルートではなく、**5 個のマスタ系 dataset の 1 つ**にすぎない。
> あわせて当初案の「課題（challenges）の第一級ハブ化」も撤回し、関心はベクトルで表す。

データの「意味」は、業界標準 **OSI（Open Semantic Interchange）v1.0** に倣った
**1 つの概念モデル YAML**（[`backend/semantic/osi_event_marketing_v1.yml`](../backend/semantic/osi_event_marketing_v1.yml)）に
集約する。これを設計の単一の思想源として、`ontology.py`（Pydantic）と Firestore を手で導出する。

- 基底は **5 マスタ**: `persons` / `accounts` / `events` / `products` / `contents`。
  いずれも対等で、Event だけが中心ではない。
- 実体（マスタ）を、用途ごとに分割した**ファクト**（`event_attendances` /
  `product_interests` ほか）が取り囲む **ファクト・コンステレーション（星座型）**。
  ファクト系は**今後さらに増える前提**で拡張可能に保つ。
- 顧客の文脈（課題・関心）は固定ラベルの課題マスタではなく、各マスタが持つ
  **`appeal_summary`（監査可能な要約テキスト）＋ `appeal_vector`（埋め込み）** で表す。
  「この人に意味的に近い content / product / event」を **コサイン類似度**（`semantic_affinity`）で
  引き当てる。類似度は決定論 Python の総当たり（`backend/semantic_search.py`）で計算する。
- 概念の正典は [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md)。

> イベントが依然として重要な分析単位であることは変わらない（集客・歩留まり・費用対効果は
> `events` の metrics に畳んでいる）。変わったのは「Event だけがルート」という前提である。

#### マスタ同一性と名前ベース照合（重複防止）

マスタ（とりわけ `events`）が重複採番されると、紐づくファクトが分裂し「過去との比較」（原則5）が
崩れる。そのため取り込み時の同一性判定を以下の決定論ルールで行う（`map_extraction` の
`event_id_resolver`、`_find_existing_event_by_name`）。この考え方は他のマスタ（accounts/persons 等）の
名寄せにも一般化する:

1. UI で `event_id` を明示指定 → その既存イベントへ追加取り込み（最優先）
2. 未指定かつ overview.txt 由来の**イベント名が既存と完全一致** → 新規採番せず
   既存 `event_id` へ統合（`merge=True` で更新）
3. いずれも該当しなければ新規採番

- **なぜ名前ベースか**: overview.txt を含むバッチを取り込むたびに `uuid` で新規採番され、
  同名イベントが重複していた（例: `event_ca04...` / `event_d66a...`）。名前は人間が
  認識するイベント同一性の最も安定したキーであり、UI 変更なしで重複を根本防止できる。
- **なぜ Python（決定論）か**: 同一性判定は業務判断であり、AI に委ねず明文化する
  （メモリ「AI/Pythonの責務境界」）。意味の正規化（名前の抽出）は AI、同一性の確定は Python。
- **Auditable AI（原則4）**: 統合が成立した場合は `EntityTransformation` に
  「同名イベントが既存 → 統合」の `TransformDecision` を記録し、DataLineage で追える。

### 原則2: あらゆるデータをオントロジーに通す（Ontology-First）

CSV・Excel・PowerPoint・PDF・テキスト、形式は問わない。
データをそのまま使うのではなく、**必ずオントロジーへのマッピングを経由する**。

- PDFやPPTは直接参照せず、意味情報を抽出してオントロジーまたはセマンティックレイヤーとして保持する
- データの「形式」は一時的なもの。「意味」だけが残る

### 原則3: エージェントはオントロジーの上で動く（Ontology-Grounded Agent）

`MarketingAgent` は生のデータではなく、**オントロジー化された知識**を入力として受け取る。
エージェントは汎用の「マーケター」であり、タスクの種類でクラスを分けない。

- メール生成も、イベント振り返り分析も、戦略提案も、同一のエージェントが指示に従って実行する
- エージェントの専門性は「マーケティング」であり「メール生成」や「分析」ではない

### 原則4: AIの判断は説明可能でなければならない（Auditable AI）

すべてのAI判断には根拠が必要。
`reason_for_inclusion` はその最初の実装例であり、今後追加するすべてのエージェント出力にも
「なぜそう判断したか」のフィールドを設ける。

- これはデバッグ機能ではなく、マーケターがAIを信頼するための仕組みである
- 根拠フィールドは `Optional` にしてはならない

#### 実装例: 加工処理レポート（データ統合パイプライン）

データアップロードは**二段階処理**で進む。両段とも「何をどう変換したか」を記録する。

1. **ステージ1（AI一次処理）**: `SchemaMapper`（CSV/Excel）/ `DocumentExtractor`（テキスト）が
   生のマッピング・抽出を出力する。結果は `DataLineage.column_mapping` / `raw_extraction` に保存。
2. **ステージ2（Python加工処理）**: `OntologyMapper` が Product 名寄せ・
   数値クレンジング・行スキップを**決定論的に**実行する（業務的判定は行わない。ADR-013）。

ステージ2の各判定は `TransformDecision`（`field` / `value` / **`reason`（非null）** / `source_signals`）
として `DataLineage.transformations` に蓄積し、スキップは `SkippedRecord`、全体集計は
`TransformationSummary` に残す。

**設計判断: 逐次ログではなくレポート（lineage 蓄積）にする。** 理由:

- ログは流れて消えるが、変換経緯はデータの来歴そのもの。後からバッチ単位で参照できる必要がある。
- 特にステージ2では変換に使った生シグナルが変換後に破棄されるため、
  記録しなければ「なぜこの値になったか」を後から再構成できない。
- `TransformDecision.reason` を非null とするのは原則4「根拠フィールドは `Optional` にしてはならない」に従うため。

取得方法: `GET /api/integration/batches/{batch_id}/report` がステージ1のAI出力・ステージ2の判定根拠・
サマリを構造化JSONで返す（UI は将来実装）。

#### AI と Python の責務境界 — 「意味を変えるか否か」で引く

二段階処理の役割分担は、**処理量の多寡ではなく「意味を変えるか否か」**で決める。

- **AI に任せてよい**: 抽出、および**意味を変えない表記正規化**（`61%→0.61`、カンマ・通貨記号の除去、
  enum 文字列の表記揃えなど）。表記の変換は判断を伴わないため AI で問題ない。
- **Python に残す（明文化・非ブラックボックス）**: **判断を伴う業務ロジック**。
  Product 名寄せ（`_match_products`）など。
  「なぜそう分類したか」が説明可能であるべき処理は、AI のブラックボックスに委ねず
  決定論的な Python（`OntologyMapper`）に明文化する。これは原則4の直接の帰結である。

#### 複数ファイルの横断処理 — ファイル間の紐付けも決定論で引く

1イベントは複数ファイルの集合で届く（例: `leads.csv` ＋ `overview.txt` ＋ `survey.txt`）。
これらを1バッチでまとめてアップロードし、ファイルをまたいで `event_id` を伝播させる
（`process_batch`）。`leads.csv` 自体はイベント名を持たないが、`overview.txt` から確定する
`event_id` のもとに Contact を紐付け、`survey.txt` の KPI/SurveyResponse も同じイベントに束ねる。

> **設計判断: 「どのファイルがどのイベントに属するか」の解決に AI を使わない。** 必要なのは
> (1) ファイルの処理順決定、(2) 確定した `event_id` の伝播 の2点で、どちらも**明示的な業務判断**。
> 原則4に従い決定論的 Python に置く。具体的には `process_batch` が非表形式（Event を生む
> `overview.txt` 等）を先に処理して `event_id` を確定し、表形式（`leads.csv`）へ伝播させる。
> `overview`/`概要` を名前に含むファイルを先頭へ寄せる安定ソートで、`survey.txt` が処理される前に
> イベントが確定するよう順序を保証する。event_id の優先順位（明示選択 > ドキュメント由来 > フォールバック）は
> `_resolve_event_id` の1箇所に集約する。AI に横断判断をさせると説明不能になるため意図的に避ける。
>
> 部分失敗は per-file の try/except で吸収し、1ファイルの失敗が他ファイルの取り込みを止めない。
> `overview.txt` が失敗した場合は `event_id` 未確定となり Contact は `events/unknown/...` に落ちる
> （データロスはせず、現状互換の劣化動作にとどめる）。バッチ横断の伝播結果は
> `GET /api/integration/batches/{id}/report` の `cross_file_summary` で確認できる。
>
> Contact の名寄せ（複数ファイルにまたがる同一人物の統合）は別の判断であり、ここには含めない。

#### Firestore のバッチ階層 — 中間ドキュメントは必ず実体化する

> ⚠️ **本節は OSI 再設計（[ADR-008](ADR.md)）で陳腐化**。`persons` をトップレベル
> `spaces/{space_id}/persons/{person_id}` へフラット化したため、下記「幽霊ドキュメント」問題は
> 構造的に発生しない（列挙は単純な `col("persons").get()`）。`batch_id` は person の通常フィールドと
> して来歴用に保持する。以下は旧ネスト構造（`events/{eid}/batches/{bid}/contacts/{cid}`）の経緯記録。

Contact は `events/{event_id}/batches/{batch_id}/contacts/{contact_id}` に保存する。
ここで陥りやすい罠が **Firestore の「幽霊ドキュメント（ghost / phantom ancestor）」** である。
リーフ（contacts ドキュメント）だけを `set` すると、中間の `batches/{batch_id}` ドキュメントは
**実体を持たない祖先パス**になる。Firestore のコレクションクエリ（`collection(...).get()`）は
**実体のあるドキュメントしか返さない**ため、`collection(".../batches").get()` でバッチを列挙できず、
コンタクトを1件も取得できない（`get_event_contacts` が無言で `[]` を返し、エージェントが
「リスト取得不能＝エラー」と誤認する事象が実際に起きた）。

> **設計判断: バッチ階層では中間ドキュメントを取り込み時に明示的に `set` する。** `process_file` は
> Contact を書き込んだ `event_id` について `events/{eid}/batches/{batch_id}` を併せて実体化する。
> 列挙側（`get_event_contacts` / `_find_contact`）は、過去に作られた幽霊バッチにも対応できるよう
> `collection(...).get()` ではなく **`list_documents()`**（祖先パスのみのドキュメント参照も返す）で
> バッチを走査する。両者は別レイヤの保険であり、片方だけでは既存データ or 将来データのどちらかが漏れる。
>
> なお Contact の所属イベントの真実の源は **`Contact.source_event_id`**（原則: 「いつ・どのイベントで」を
> 常に保持）であり、保存パスの `event_id` はこれと一致させる。将来コンタクトをイベント横断で引く場合は
> バッチ階層の走査ではなく `source_event_id` への collection group query に寄せる選択肢もある
> （その場合はインデックス追加が必要）。

#### 実装上の注意 — 構造化出力とスキーマの定め方

Gemini の構造化出力（`response_schema` / controlled generation）は、**プロパティが定義されていない
自由形式の `dict` を埋められず空にする**（結果として「`status=done` なのに生成0件」という無言の失敗になる。
この事象は上記の加工処理レポート機能が検知した）。そのため:

- **キーが可変な箇所**（CSVカラム→フィールドの `column_map` など）: `response_schema` を使わず
  JSONモード（`response_mime_type="application/json"` のみ）で自由 JSON を受け取り Python でパースする。
- **フィールド集合が既知の箇所**（テキストからのエンティティ抽出など）: **固定キーの具体 Pydantic モデル**
  でスキーマを定義する（`marketing_agent` の `EmailBlock` が動く前例）。

### 原則5: 過去が未来を照らす（Historical Intelligence）

単発ツールではなく、イベントをまたいだ蓄積・比較・学習が価値を持つ。

- 「今回の展示会のアポ獲得数は過去3回と比べてどうか」
- 「この費用対効果は前回比でどう変わったか」
- 時系列のイベント記録を保持し、エージェントが参照できる設計にする

### 原則6: テナント分離は規律ではなく構造で担保する（Context-Bound Data Access）

本プラットフォームはマーケティングチーム単位の **スペース（Space）** でマルチテナント分離する。
スペース間のデータ分離を「毎回フィルタを書き忘れない」という *規律* ではなく、
「スコープ外の参照を手にする経路が存在しない」という *構造* で担保する。これは
**Context Object パターン**（リクエストの実行文脈を明示オブジェクトに集約）に
**ケイパビリティ・ベースのアクセス制御**（権限を“持っている参照”としてのみ表現）を
組み合わせた設計モデルである。

4つの設計判断:

1. **コンテキストの集約（Context Object）** — 「誰が・どのスペースで・どの権限で」を
   `SpaceContext`（`space.py`）という単一の不変オブジェクトに集約する。space_id 文字列を
   関数引数として各所に撒くアンビエントな受け渡しを禁止する。

2. **ケイパビリティとしてのデータ参照（Capability, not coordinate）** — `SpaceContext` は
   space_id という座標ではなく、「自スペースにしか到達できないスコープ済み参照を返す能力」を
   運ぶ。業務コードが触れるのは `space.col("events")`（= `spaces/{id}/events` に前置済み）や
   `space.scoped_db()`（ScopedClient）だけで、スコープされていない生の参照を手にする手段がない。

3. **トラスト境界での一度きりの束縛（Bind once at the edge）** — テナント確定は
   `dependencies.get_space_context`（FastAPI ディペンデンシ＝トラスト境界）でちょうど一度だけ
   行う。トークン検証とメンバーシップ照合をここで済ませ、下流（ルーター・パイプライン・
   AIツール）はテナンシーを再判断しない。認可判断の分散を防ぎ、監査点を一点に集約する。

4. **生アクセス経路の除去（Forcing function）** — 業務コードから `firestore.client()` の
   直接呼び出しを撤廃し、`SpaceContext` を唯一のサンクション済み入口とする。スコープ漏れは
   レビューの注意力ではなく設計の構造によって不可能になり、逸脱は一目で検出できる。

#### AI境界への拡張: 最小権限の段階的縮約（原則4「Auditable AI」の統治的拡張）

AIツール（`marketing_agent.make_tools`）には `SpaceContext` よりさらに狭いケイパビリティを
与える。スコープ済み `ScopedClient` を closure で捕捉し、ツールのシグネチャから space_id を
排除する。リクエストハンドラは「自スペースを指定できる」が、AIツールは「自スペースしか
触れない（他スペースを名指しすることすら不可能）」。**アクセス制御がAIの判断（正しい
event_id/フィルタを選ぶこと）に一切依存しない** ことを構造的に保証する。

#### セキュリティ方針: スペースIDの信頼境界（Space-ID Trust Boundary）

クライアント（ユーザー／AI）が提示する space_id・role は **「主張（claim）」であって
「権限」ではない**。権限は毎リクエスト、改ざん不能な検証済み uid（Firebase署名）と
サーバ保持の `spaces/{id}/members/{uid}` ドキュメントから再導出する。

- `X-Space-Id` ヘッダ／localStorage の activeSpaceId は **非信頼**。サーバは membership で照合し、
  無ければ 403。他人のスペースIDをヘッダに入れても、その uid の membership が無い限り弾かれる。
- membership は **Admin SDK 経由の owner 専用フローでのみ** 書込可（`firestore.rules` で
  クライアント書込を全拒否）。スペース作成時の owner も検証済み uid から決定する。
- データパスが `spaces/{id}/...` で構造的に閉じるため、他スペースのIDを指しても 404
  （横断参照が構造上不可能）。
- 認可の真実を membership ドキュメントに置くことで、メンバー削除は次リクエストで即 403
  （トークン有効期限を待たない即時失効）。

#### メータリング: リソース消費の生実績のみを計測しクレジットへ換算する

将来の課金のため、**原価に直結するリソース消費の生実績のみ**を計測する（`metering.py`）。
機能単位メトリクス（メール生成数・取込数など）は取らない（メール生成は自由に動く
エージェントの一挙動にすぎず、課金対象の機能として定義されていないため）。

- 計測対象は (a) LLM利用（モデル種別ごとの入出力トークン）と (b) コンピュート利用
  （リソース種別ごとの実行時間 ms）の2種のみ。`spaces/{id}/usage/{YYYY-MM}` にネスト Increment。
- 課金は **単一の「クレジット」概念**。生実績に換算レート（`plans.py`）を掛けて
  読み取り時に算出する（保存しない＝レート改定が遡及的・一貫して反映）。プランは
  Free/Pro/Premium を単一の月次クレジット上限として定義する（トークン/時間を別々に持たない）。

### 原則7: 個別対応はセグメント方式で（AIが設計し、人が承認し、組み立ては決定論）

ハウスリストへの個別カスタマイズ（メール等）は、**全コンタクトに1通ずつフル生成しない**。
それは処理時間・トークンともに無駄が大きく、定型作業を過剰にプログラム化することにもなる。
代わりに「少数のセグメントに切り分け → セグメント単位でパターンを作り → 各メールは決定論的に
組み立てる」という構造を取る。

価値の核は、**曖昧な自然言語の意図をエージェントがマーケ知見で読み解き、ワークフロー全体を
自律的に組み立てて実行する**こと。人間に「セグメントを設計せよ→分類せよ…」と段階指示を
出させない。したがってこれは人間駆動のウィザードではなく、エージェントが自分の判断で呼ぶ
**薄いツール群**（`define_segment` / `assign_segment` / `generate_patterns` / `run_assembly`）で
構成し、オーケストレーションの知能は**システムプロンプトのマーケ方法論**に置く（固定パイプライン
としてハードコードしない）。

> **背景思想**: この方法論の背景にあるマーケ設計思想が
> [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md)（**Static Core & Dynamic Context**）。
> そこでの **情報3階層（L1大黒柱 / L2中柱 / L3ドア）** が本原則に対応し、**L3＝CEP（顧客の悩み）を
> 束ねたものが Segment、各 Contact が L3** にあたる。AIが生成してよいのは L3（動的な文脈）だけで、
> L1/L2（自社・機能の本質価値＝不変のコア）は固定する。パターン生成のガードレール
> （捏造禁止／1機能×1CEP／ブランド資産の維持）も同ドキュメントで定義し、`marketing_agent.py` の
> プロンプトに実装している。

責務境界（原則4・「意味を変えるか否か」）に従い、各段の置き場所を分ける:

| 段 | 担い手 | 理由 |
|---|---|---|
| セグメント軸の設計（どう切るか） | **AI**（プロンプトの方法論） | マーケ判断。固定ルール化しない |
| バケットへの分類（`segmentation.py`） | **決定論Python**＋意味判断のみ軽量AI | 業務判断は明文化・監査可能（reason 必須） |
| コンテンツパターンの文面生成 | **AI** | 文章作成はAIの中核能力。バケット数 K 回のみ |
| 各メールの組み立て（プレースホルダ置換） | **決定論Python**（LLMゼロ） | 定型作業はプログラムで高速・無コスト |

**HIL（Human-In-the-Loop）**: 自律実行でも、各ゲート（①軸の決定 ②分類結果 ③パターン）で
提案と根拠を提示し、**ユーザーの承認を得てから確定ツールを呼ぶ**。とくに `run_assembly`
（全件確定）は明示承認を必須とする。HILは固定ステートマシンではなくシステムプロンプトの
指示＋会話ターンで薄く実現し、軌道修正（軸変更・対象絞り込み・トーン変更）は該当段のやり直しで
吸収する。成果物（Segment・割り当て根拠・パターン）はすべて Firestore に残り Auditable。

> 反面教師として、旧実装は `compose_emails` ツールが run を作るだけで、別経路の
> `_execute_email_run` がコンタクトごとにフルLLM生成していた（文体ルールも二重定義、
> エージェントは自作物を参照不能）。これは「AIに任せれば十分なものを過剰にプログラム化し、
> 運用負荷を高める」アンチパターンであり、本原則で置き換えた。

---

## 3. 3層アーキテクチャ

> ⚠️ ここでの「3層」は**技術スタックの層**（Data Integration / Event Ontology / Marketing Agent）。
> マーケティングの**メッセージ情報3階層（L1大黒柱 / L2中柱 / L3ドア）**は別物で、
> [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) を参照（原則7とも対応）。

```
┌──────────────────────────────────────────────────────────┐
│              Layer 3: Marketing Agent Layer               │
│                                                           │
│        MarketingAgent（単一・汎用 / ADK Agent + Tools）    │
│           入口: chat_stream（チャット, SSE ストリーム）     │
│  ─────────────────────────────────────────────────────── │
│  タスク（エージェントの種類ではなく「指示」で切り替わる）      │
│  ・個別対応: セグメント方式（軸設計→分類→パターン→組み立て）  │
│  ・イベント振り返り分析（get_space_data+run_python_code）    │
│  ・戦略レポート保存（save_report, 実装済み）                 │
│  Tools: get_space_data / run_python_code /                │
│    find_relevant_for_person / save_report /               │
│    define_segment / assign_segment /                      │
│    generate_patterns / run_assembly                       │
├──────────────────────────────────────────────────────────┤
│        Layer 2: Semantic Layer (OSI v1.0 / 星座型)        │
│                                                           │
│  5 マスタ: persons / accounts / events / products /       │
│            contents（各 appeal_summary + appeal_vector）  │
│  ファクト: event_attendances / product_interests / …      │
│  意味的近接: semantic_affinity（appeal_vector のコサイン） │
│  出力: segments(動的)→snapshots(版)→assignments /          │
│        deliverable_patterns(雛形) / deliverables(成果物)     │
│  稼働ログ: integration_jobs（旧 data_lineage）              │
│  正典 YAML: backend/semantic/osi_event_marketing_v1.yml   │
├──────────────────────────────────────────────────────────┤
│              Layer 1: Data Integration Layer              │
│                                                           │
│  DataIntegrationAgent（process_file）                     │
│  パスA 表形式(CSV/Excel): run_schema_mapper ─┐            │
│  パスB 非構造化(Text):    run_document_extractor ─┤        │
│                                  ↓ ステージ2          │
│                       OntologyMapper（決定論的Python変換）  │
└──────────────────────────────────────────────────────────┘
```

---

## 4. オントロジーエンティティ（OSI dataset → Pydantic）

> **正典は YAML**: 概念モデルの真実源は
> [`backend/semantic/osi_event_marketing_v1.yml`](../backend/semantic/osi_event_marketing_v1.yml)。
> 各 dataset が `ontology.py` の Pydantic モデルに、dimension/enum がフィールドに、identifier が FK に
> 手で対応する（[ADR-008](ADR.md) / [SEMANTIC_LAYER.md](SEMANTIC_LAYER.md)）。

### マスタ系（5 dataset — いずれも対等な基底）

`Person` / `Event` / `Product` / `Content` は **`appeal_summary: str`（監査可能な要約テキスト）と
`appeal_vector: list[float]`（その埋め込み）** を持ち、意味的近接（`semantic_affinity`）で結びつく。

| エンティティ | 種別 | 説明 |
|---|---|---|
| `Account` | Model | 企業・組織（業種 `industry_type` / 規模 `company_size`）。person の所属先 |
| `Person` | Model | 個人（ハウスリストの1人）。旧 `Contact` を正規化。`account_id` で企業に所属。**appeal_summary / appeal_vector** |
| `Event` | Model | 主催・管理するイベント。実績（KPI/NPS/費用）は dataset の metrics へ畳む。**appeal_summary / appeal_vector** |
| `Product` | Model | 自社製品（旧 `Product` enum を実体化: `product_id` / `product_category` / `product_name`）。**appeal_summary / appeal_vector** |
| `Content` | Model | マーケ素材（WP・事例・ウェビナーアーカイブ・募集中セミナー）。旧 `ContentAsset`。**appeal_summary / appeal_vector** |
| `PersonStage` | Enum | 顧客の段階（LEAD / MQL / SQL / CUSTOMER / EXCLUDED）。旧 `ContactStage` |
| `EventType` / `EventStatus` | Enum | 展示会・セミナー・プライベートイベント / 計画中・開催中・終了 |
| `ContentType` | Enum | 資料・ホワイトペーパー / 導入事例 / ウェビナーアーカイブ / 未来のセミナー / 未来のイベント |

> **課題（Challenge）系は撤回**: 当初案の `Challenge` マスタと `PersonChallenge` / `ProductChallenge` /
> `ContentChallenge` ブリッジは作らない（[ADR-008](ADR.md)）。顧客の課題・関心は固定ラベルでなく
> 各マスタの `appeal_summary` / `appeal_vector` が担い、`semantic_affinity` のコサイン類似度で結ぶ。

### ファクト系（行動ログ。今後さらに増える）

| エンティティ | 種別 | 説明 |
|---|---|---|
| `EventAttendance` | Model | 申し込み・参加（person×event）。`action_type`（申し込み/参加/アンケート高評価） |
| `ProductInterest` | Model | 製品への興味・商談（person×product）。`interest_status`（興味あり/デモ実施済み/キャンセル） |

> **旧集計エンティティ**: `EventKPI` / `SurveyResponse` / `CostItem`（集計）は独立モデルにせず、
> `events` dataset の **metrics**（`total_visitors` / `nps_score` / `total_cost` / `cost_per_acquisition` 等）へ
> 畳み込む。費用明細・アンケート自由記述が必要になった時点で `event_costs` / `survey_verbatims` を fact 化する。

#### セグメント・成果物（個別カスタマイズ）

セグメントは**動的定義 → 静的スナップショット（版）→ メンバー**の3層。成果物は `format` を持つ
`Deliverable`（メールは一形態）。雛形は segment×bucket×format 単位。

| エンティティ | 種別 | 説明 |
|---|---|---|
| `Segment` | Model | **動的セグメント**（軸・バケット・criteria の定義）。確定メンバーではない |
| `SegmentSnapshot` | Model | **静的スナップショット**（施策時点で凍結したメンバーの版。1 segment に複数版） |
| `SegmentAssignment` | Model | スナップショット配下のメンバー（`person_id`→`bucket`）。**`reason` 必須**。`snapshot_id` を保持 |
| `DeliverablePattern` | Model | **雛形**（segment×bucket×`format` の生成テンプレート, 人手編集可） |
| `Deliverable` | Model | **個別カスタマイズ成果物**（person 単位）。`format`(EMAIL/TALK_SCRIPT/PROPOSAL…)・`run_id`/`snapshot_id`/`pattern_id` を参照。旧 `ComposedEmail` を一般化 |
| `DeliverableBlock` | Model | 成果物の構成単位。包含根拠（`reason_for_inclusion`）を必須で持つ。旧 `EmailBlock` |
| `DeliverableFormat` | Enum | 成果物の形式（EMAIL / TALK_SCRIPT / PROPOSAL …。今後増える） |

> **成果物は person 単位、雛形は segment 単位**: 成果物（`Deliverable`）は person ごとに保存する
> （成果物だから）。所属セグメント/バケットは `snapshot_id`/`bucket` 経由で推移的に辿る。バケット×format
> ごとの雛形は `DeliverablePattern` に分けて持つ（`segments/{sid}/patterns/{bucket}__{format}`）。

#### 取り込みジョブ記録・加工処理レポート（Auditable AI / 原則4）

二段階処理の「何をどう変換したか」を記録する。**これは独立した『来歴オブジェクト』ではなく、取り込み
エージェントの稼働ログ**である。個々のデータの出自は各レコードの `source_job_id` / `source_file_id`
（inline フィールド）から逆引きする。詳細は原則4の実装例を参照。

| エンティティ | 種別 | 説明 |
|---|---|---|
| `IntegrationJob` | Model | バッチ単位の取り込みジョブ記録（旧 `DataLineage`）。ステージ1出力とステージ2の加工根拠を束ねる |
| `ColumnMappingResult` | Model | パスA（`run_schema_mapper`）の出力: CSVカラム → フィールドのマッピング |
| `DocumentExtractionResult` | Model | パスB（`run_document_extractor`）の出力: 抽出エンティティ群 |
| `TransformDecision` | Model | ステージ2の1判定。`reason`（非null）で根拠を保持 |
| `EntityTransformation` | Model | 1エンティティ生成時の判定集合 |
| `SkippedRecord` | Model | エンティティ化されなかった入力とその理由 |
| `TransformationSummary` | Model | バッチ単位の加工サマリ |

### Person と PersonStage の関係

```
Person（ハウスリストの1人）
  ├── account_id        ← 所属企業（Account への参照）
  ├── stage: PersonStage
  │     LEAD（展示会等で出会った段階）
  │     MQL / SQL / CUSTOMER / EXCLUDED
  ├── ← EventAttendance（多対多: 参加したイベント群）
  ├── ← ProductInterest（多対多: 興味のある製品群）
  ├── appeal_summary: str         ← 関心・悩み・文脈の要約（監査可能 / CEP の接地）
  └── appeal_vector: list[float]  ← appeal_summary の埋め込み（意味検索用）
```

- `Lead` は独立エンティティではなく、`PersonStage` の値のひとつ
- 温度感・興味度の観測事実は `EventAttendance.challenge_note`（テキスト）が担う（取り込み時の分類はしない。ADR-013）
- **出会った／参加したイベントは fact dataset `EventAttendance`（person×event の多対多）で保持する。**
- **抱える課題・関心は固定ラベルでなく `appeal_summary` / `appeal_vector` が担う（旧 PersonChallenge は撤回）。**

> **設計変更（ADR-008）**: 旧モデルは「1コンタクト = 主に1つの出会いイベント」とし、接触を
> `Contact.source_event_id` の単純参照で持ち、独立エンティティ化を避けていた。OSI 化に伴い、参加は
> `EventAttendance`（`action_type` を持つファクト）として正規化し、**多対多の接触履歴を第一級で表現する**。
> 旧「`EventInteraction` を作らない」判断はここで反転した（参加は star schema の fact そのもの）。

### Event と Content の関係（セミナーのデュアル性）

開催予定のセミナー/イベントは「主催・管理する対象」と「案内するコンテンツ」の二面性を持つ。

```
Event（主催・管理）               Content（案内・推薦）
  ├── KPI / 費用 / 参加者(metrics)   ├── url
  └── event_status: 終了/計画中       ├── content_type: 未来のセミナー（募集中）
                                    └── linked_event_id: Optional[str]  ← リンク（任意）
```

| データ種別 | Event | Content |
|---|---|---|
| 開催済みイベント | ✓ | ✗ |
| 開催予定のセミナー/イベント | ✓（管理用） | ✓（案内用） |
| ホワイトペーパー・事例・ウェビナーアーカイブ | ✗ | ✓ |

### 意味的近接（semantic_affinity）— CEP のベクトル化

```
                 cosine 類似度（決定論 Python の総当たり）
Person.appeal_vector ──▶ Content.appeal_vector
                    └──▶ Product.appeal_vector
                    └──▶ Event.appeal_vector
```

person の関心・悩み（CEP）を `appeal_summary` に要約 → 埋め込み（`appeal_vector`）し、offer 側
（content / product / event）の `appeal_vector` との**コサイン類似度上位**を「この人に合うもの」として
案内する。固定ラベルの課題マスタでは表せない、課題に収まらない興味・文脈まで連続的に扱える。
これは [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) の **L3=CEP（動的文脈）** を、固定ラベルでなく
意味空間として接地したもの。計算は [`backend/semantic_search.py`](../backend/semantic_search.py) の
`find_similar`（Firestore のベクトルインデックス・`find_nearest` は使わない）。一致根拠は双方の
`appeal_summary` を並べて説明する（Auditable AI）。

---

## 5. 正規命名一覧

### 5.1 エージェント名

| 正規名 | ファイル | 役割 |
|---|---|---|
| `DataIntegrationAgent` | `agents/data_integration_agent.py` | あらゆる形式のデータをオントロジーにマッピング |
| `MarketingAgent` | `agents/marketing_agent.py` | オントロジーを元にマーケターとして活動（汎用） |

**DataIntegrationAgent の構成**: 「エージェント」は概念単位であり、実体は関数群。

- バッチ入口: `process_batch`（複数ファイルを横断処理し、ファイル間で `event_id` を伝播）
- ファイル入口: `process_file`（1ファイルを処理。パスA/Bを振り分け、ステージ2を実行）
- パスA（表形式 CSV/Excel）: `run_schema_mapper` 関数 — カラムマッピングをAIで生成
- パスB（非構造化 Text）: `run_document_extractor` 関数 — エンティティをAIで抽出
- ステージ2（決定論的変換）: `OntologyMapper` **クラス**（`agents/ontology_mapper.py`）

> ※ `SchemaMapper` / `DocumentExtractor` はパスの呼称（コメント上のラベル）であり、クラス名ではない。
> 実体は上記の関数。

**MarketingAgent の構成**: Google ADK の `Agent`（`agents/marketing_agent.py` の `build_agent`）として実装。
ツールはモジュールグローバルではなく `make_tools(db, space)` のファクトリで生成し、スペース前置済みの
`ScopedClient` を closure で束縛する（リクエストごとに `build_agent` で構築）。

- 入口は `chat_stream`（チャット、Server-Sent Events でストリーミング）
- データ分析は Code Interpreter に集約（ADR-009）: `get_space_data`（全エンティティを
  Firestore→Pydantic→DataFrame→CSV でサンドボックスへ投入）と `run_python_code`（隔離
  サンドボックスで LLM 生成 Python を実行）の2本。旧来の読み取りツール群
  （`list_events` / `get_event_*` / `get_content_catalog` 等）はこの2本に統合・廃止した。
- 「この人に合うもの」を引く意味検索は `find_relevant_for_person`（appeal_vector のコサイン
  近接）。レポート保存は `save_report`。
- 個別対応はセグメント方式（原則7）。`define_segment`（軸登録）/ `assign_segment`（分類）/
  `generate_patterns`（バケット別パターン生成）/ `run_assembly`（決定論的に全件組み立て）を
  エージェントがHILで承認を取りつつ順に呼ぶ。組み立てはLLMを使わず高速。

> **設計判断: なぜチャット＋ツール方式か。** 原則3「エージェントはタスクの種類でクラスを分けない」を実装に落とすと、
> 「メール生成API」「分析API」のように出口を固定するのではなく、ユーザーの指示（チャット）に対して
> エージェント自身がどのツール（=オントロジーのどの部分）を使うかを選ぶ形になる。
> タスクが増えても増えるのはツールであり、エージェントの口は1つ（チャット）のまま保たれる。

### 5.2 APIエンドポイント

役割ごとに3つのルーターに分離する（`routers/integration.py` / `routers/marketing.py` / `routers/events.py`）。

#### データ統合（`/api/integration` — `DataIntegrationAgent`）

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/integration/batches` | POST | 複数ファイルアップロード → バッチ開始（`files` で複数受け取り） |
| `/api/integration/batches/{id}` | GET | バッチ状態の取得（ファイルごとの `files` 進捗・`resolved_event_id`・`partial` を含む） |
| `/api/integration/batches/{id}/report` | GET | 加工処理レポート（ファイルごとの `reports` ＋ 横断伝播の `cross_file_summary`） |
| `/api/integration/batches/{id}/contacts` | GET | バッチ内コンタクト一覧 |

#### マーケティング（`/api/marketing` — `MarketingAgent`）

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/marketing/chat` | POST | エージェントとのチャット（SSE ストリーム）。タスクはここから指示する |
| `/api/marketing/runs/{id}` | GET | メール組み立てランの状態取得 |
| `/api/marketing/runs/{id}/results` | GET | 組み立てられたメール一覧 |
| `/api/marketing/runs/{id}/export` | GET | 結果をCSVでエクスポート |

> **設計判断: run はツールが作り、その場で完了する。** メールは `run_assembly` ツールが
> 決定論的に組み立てる（LLM不使用）ため、run は作成時点で `status=done`。旧設計の
> `POST /runs/{id}/execute`（バックグラウンド起動）は廃止し、フロントは状態取得・結果取得・
> CSVのみ行う。SSE は `tool_result` イベントでツール戻り値（`run_id` 等）も流すため、
> フロントは本文の正規表現に頼らず `run_id` を取得できる。

#### セグメント（`/api/segments` — `MarketingAgent` が作る成果物の閲覧/編集）

施策向けセグメントと割り当て・パターンは、エージェントのツールが作成する。本RESTは人間が
後追い・介入するための薄い窓口（フローは駆動しない）。原則7を参照。

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/segments` | GET | 登録済みセグメント一覧 |
| `/api/segments/{id}` | GET | セグメント定義＋割り当て結果（根拠つき）・バケット別人数 |
| `/api/segments/{id}/patterns` | GET | バケット別コンテンツパターン一覧（レビュー用） |
| `/api/segments/{id}/patterns/{bucket}` | PUT | 生成済みパターンの編集・上書き（HILの介入窓口） |

#### イベント（`/api/events` — オントロジー直接参照）

`MarketingAgent` のツールが内部参照する Firestore を、フロントエンドの Sources パネル用に HTTP でも公開する。

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/events` | GET | 登録イベント一覧（日付降順） |
| `/api/events` | POST | 新規イベント作成 |
| `/api/events/{id}` | GET | イベント詳細 |
| `/api/events/{id}` | PUT | イベント更新 |
| `/api/events/{id}/kpi` | GET | KPI 取得 |
| `/api/events/{id}/survey` | GET | アンケート集計取得 |
| `/api/events/{id}/costs` | GET | 費用明細と集計（`CostSummary`）取得 |

### 5.3 キー概念

| 概念 | コード上の名前 | 説明 |
|---|---|---|
| **包含根拠** (Inclusion Rationale) | `reason_for_inclusion` | AI判断の説明文。すべてのエージェント出力に必須。`Optional` 不可 |
| **加工判定根拠** (Transform Reason) | `TransformDecision.reason` | ステージ2の各変換の根拠。`Optional` 不可（原則4） |
| **コミュニケーション戦略** (Communication Strategy) | `_SYSTEM_PROMPT` 内のブロック選択ルール | セグメントのバケットに基づくブロック構成ルール |
| **バッチ** (Batch) | `batch_id` | 1回のデータ統合セッション。**複数ファイルを束ねた1アップロード**（同一イベントに属するファイル群を横断処理する単位） |
| **ラン** (Run) | `run_id` | 1回のメール組み立て実行。`run_assembly` ツールが決定論的に作成し即完了 |
| **セグメント** (Segment) | `segment_id` | 施策向けの分類軸。`define_segment` で登録、`assign_segment` でコンタクトをバケットへ分類（原則7） |
| **バケット** (Bucket) | `SegmentAssignment.bucket` | セグメントの運用単位の値（多軸なら直積セル）。バケット単位でコンテンツパターンを生成 |
| **チャットセッション** (Chat Session) | `session_id` | `MarketingAgent` との対話セッション。ADK の `InMemorySessionService` で管理 |
| **コンテンツカタログ** (Content Catalog) | Firestore `contents` コレクション | 推薦可能なコンテンツ（Content）。`get_space_data`（`contents.csv`）で取得、`find_relevant_for_person` で意味検索 |
| **不変のコア / 動的な文脈** (Static Core / Dynamic Context) | `_SYSTEM_PROMPT` の【ブランドの一貫性】ブロック | マーケ思想。L1/L2（自社・機能の本質価値）は固定、L3（顧客の悩み＝CEP）はAIが生成。定義は [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) |
| **情報3階層** (L1大黒柱 / L2中柱 / L3ドア) | （ドキュメント概念） | メッセージ・情報のツリー構造。技術3層（§3）とは別物。[`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) §2 |
| **CEP**（Category Entry Point） | `SegmentAssignment.bucket` 等で具現 | 顧客が購買を想起する具体的な悩み・状況。L3に対応 |

> マーケ用語（CEP / AEO / EBM / DBA）の定義は [`MARKETING_PHILOSOPHY.md` 用語集](MARKETING_PHILOSOPHY.md) に集約する（システム側は重複定義しない）。

### 5.4 日英使い分けルール

| 種別 | 言語 | 例 |
|---|---|---|
| コード識別子（クラス名・変数名・APIパス・Enumキー） | 英語 | `ContactStage.APPOINTMENT_BOOKED` |
| ユーザー向けラベル・Enumの値 | 日本語 | `= "アポ獲得済み"` |

この分離は意図的。コードとUIラベルの変更ライフサイクルが異なるため。

---

## 6. 命名で避けるべきパターン

- ❌ `generator`, `template` をシステム名やクラス名に使わない
- ❌ `ingestion`, `execution` をエージェントやルーター名に使わない（APIパスを除く）
- ❌ タスクの種類（メール生成・分析・提案）でエージェントクラスを分けない
- ❌ `job_id` と `batch_id` を混在させない。バッチ処理には `batch_id` を、メール生成実行には `run_id` を使う
- ❌ `Lead` を独立エンティティとして扱わない。`PersonStage.LEAD` が正しい
- ❌ 人・企業・興味製品・参加イベントを 1 実体（旧 `Contact`）に詰め込まない。`Person`＋`Account`＋
  ファクト（`EventAttendance` / `ProductInterest`）へ正規化する（ADR-008）
- ❌ Event を唯一のルートとして扱わない。5 マスタは対等（旧 Event-Centric は撤回）
- ❌ 顧客の課題・関心を固定ラベルのマスタ＋多対多ブリッジ（旧 `Challenge` / `*Challenge`）で持たない。
  各マスタの `appeal_summary` / `appeal_vector` と `semantic_affinity`（コサイン類似度）で表す（ADR-008）
- ❌ マーケティングアクションを「専用API」として増やさない。エージェントへの指示（チャット）＋ツールで表現する
- ❌ 業務コードで `firestore.client()` を直接呼ばない。データアクセスの唯一の入口は
  `SpaceContext`（`space.col` / `space.scoped_db`）。生クライアントの再導入はテナント分離を
  壊す（原則6 Context-Bound Data Access）
- ❌ space_id を AIツールの引数として渡さない。AIツールは closure 束縛のスコープ済み参照のみを
  持ち、space_id を表現できないようにする（Space-ID Trust Boundary）
- ❌ メール生成数・取込数などの機能単位メトリクスを課金目的で計測しない。計測するのは
  リソース消費の生実績（トークン・処理時間）のみ。課金はそこからの換算クレジットで扱う

---

## 7. 旧名→新名の対応表（移行メモ）

| 旧名 | 新名 | 種別 |
|---|---|---|
| `StructuredLead` | `Contact` | Pydanticモデル |
| `LeadSegment` | `EngagementLevel`（その後 ADR-013 で廃止） | Enum |
| `TotalTailoredEmail` | `ComposedEmail` | Pydanticモデル |
| `ContentItem` | `ContentAsset` | Pydanticモデル |
| `ProductSegment` | `Product` | Enum |
| `CONTENT_LIBRARY` | `CONTENT_CATALOG` | 定数 |
| `CONTENT_LIBRARY_BY_ID` | `CONTENT_CATALOG_BY_ID` | 定数 |
| `ingestion_agent.py` | `data_integration_agent.py` | ファイル |
| `execution_agent.py` | `marketing_agent.py` | ファイル |
| `message_generator.py` | （削除） | ファイル（レガシー） |
| `routers/ingest.py` | `routers/integration.py` | ファイル |
| `routers/execute.py` | `routers/marketing.py` | ファイル |
| `routers/generate.py` | （削除） | ファイル（レガシー） |
| `POST /api/ingest` | `POST /api/integration/batches` | APIパス |
| `GET /api/batches/{id}` | `GET /api/integration/batches/{id}` | APIパス |
| `GET /api/batches/{id}/leads` | `GET /api/integration/batches/{id}/contacts` | APIパス |
| `GET /api/execute/{id}/status` | `GET /api/marketing/runs/{id}` | APIパス |
| `GET /api/execute/{id}/emails` | `GET /api/marketing/runs/{id}/results` | APIパス |
| `GET /api/execute/{id}/download` | `GET /api/marketing/runs/{id}/export` | APIパス |
| `EventInteraction`（旧・廃止案） | `EventAttendance`（fact dataset として正規化）※ADR-008 で復権 | エンティティ |
| `Contact` | `Person` ＋ `Account`（個人と企業に正規化） | Pydanticモデル |
| `Contact.source_event_id` | `EventAttendance`（person×event の多対多 fact） | フィールド→エンティティ |
| `Contact.interested_products` | `ProductInterest`（person×product の多対多 fact） | フィールド→エンティティ |
| `Contact.extracted_challenge` | `Person.appeal_summary`（要約テキスト）＋ `appeal_vector`（埋め込み） | フィールド→意味ベクトル |
| `Challenge` / `PersonChallenge` / `ProductChallenge` / `ContentChallenge`（当初案） | （撤回）→ 各マスタの `appeal_summary` / `appeal_vector` ＋ `semantic_affinity` | エンティティ→意味ベクトル |
| `ContactStage` | `PersonStage` | Enum |
| `Product`（enum） | `Product`（モデル化）＋ `product_category` | Enum→Model |
| `ContentAsset` | `Content` | Pydanticモデル |
| `EventKPI` / `SurveyResponse` / `CostItem`（集計） | `events` dataset の metrics へ畳み込み | モデル→metrics |
| `ComposedEmail` | `Deliverable`（`format` 付き, person 単位）。`marketing_runs/{run}/emails`→`/deliverables` | Pydanticモデル |
| `EmailBlock` | `DeliverableBlock`（`reason_for_inclusion` 維持） | Pydanticモデル |
| `SegmentAssignment`（`segments/{sid}/assignments`） | `segments/{sid}/snapshots/{snap}/assignments`（snapshot 配下へ） | 保存パス |
| `segments/{sid}/patterns/{bucket}`（雛形, 暗黙） | `DeliverablePattern`（`{bucket}__{format}`, format 対応で実体化） | 暗黙→Model |
| （なし） | `SegmentSnapshot`（動的セグメントの施策時点・静的版） | 新規Model |
| `DataLineage` | `IntegrationJob`（取り込みエージェントの稼働ログ）＋各レコード inline の `source_job_id` | モデル＋来歴inline化 |
| `POST /api/execute` / `POST /api/marketing/runs`（ラン作成） | （廃止）→ `run_assembly` ツール（チャット内 HIL 承認後にエージェントが呼ぶ。REST のラン作成口は持たない） | APIパス |
| `CONTENT_CATALOG`（静的定数） | （廃止）→ Firestore `contents` コレクション | データソース |
