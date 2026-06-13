# システム思想と命名規約

## 1. このプラットフォームとは何か

このシステムは **イベントマーケティングAIプラットフォーム** である。

展示会・セミナー・イベントを中心に、カオスなマーケティングデータをオントロジーに統合し、
AIエージェントがそのオントロジーの上でマーケティング活動を行うプラットフォームを目指す。

**現在の実装範囲**:

- **データ統合**: 展示会リストのCSV/Excel、イベント概要・KPI・費用・アンケートのテキストをアップロードし、
  二段階処理（AI一次処理 → Python加工処理）でオントロジー（Event / Contact / EventKPI / SurveyResponse /
  CostItem / ContentAsset）に統合する。来歴（DataLineage）と加工根拠も記録する。
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

### 原則1: イベントが中心概念（Event-Centric）

展示会・セミナー・イベントが、すべてのデータと知識の中心軸。
リード、コスト情報、アンケート、コンテンツ資産——すべて「あのイベント」という文脈の上にある。

- オントロジーのルートエンティティは `Event`
- 孤立したデータではなく、「いつ・どのイベントで」という文脈を常に保持する

#### イベント同一性と名前ベース照合（重複防止）

`Event` がルートである以上、同じイベントが重複して採番されると、配下のコンタクト・
KPI・費用が分裂し「過去との比較」（原則5）が崩れる。そのため取り込み時の
イベント同一性判定を以下の決定論ルールで行う（`map_extraction` の `event_id_resolver`、
`_find_existing_event_by_name`）:

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
2. **ステージ2（Python加工処理）**: `OntologyMapper` が EngagementLevel 判定・Product 名寄せ・
   notes 集約・数値クレンジング・行スキップを**決定論的に**実行する。

ステージ2の各判定は `TransformDecision`（`field` / `value` / **`reason`（非null）** / `source_signals`）
として `DataLineage.transformations` に蓄積し、スキップは `SkippedRecord`、全体集計は
`TransformationSummary` に残す。

**設計判断: 逐次ログではなくレポート（lineage 蓄積）にする。** 理由:

- ログは流れて消えるが、変換経緯はデータの来歴そのもの。後からバッチ単位で参照できる必要がある。
- 特にステージ2では判定に使った生シグナル（`__engagement_signal` 等）が分類後に破棄されるため、
  記録しなければ「なぜこの engagement になったか」を後から再構成できない。
- `TransformDecision.reason` を非null とするのは原則4「根拠フィールドは `Optional` にしてはならない」に従うため。

取得方法: `GET /api/integration/batches/{batch_id}/report` がステージ1のAI出力・ステージ2の判定根拠・
サマリを構造化JSONで返す（UI は将来実装）。

#### AI と Python の責務境界 — 「意味を変えるか否か」で引く

二段階処理の役割分担は、**処理量の多寡ではなく「意味を変えるか否か」**で決める。

- **AI に任せてよい**: 抽出、および**意味を変えない表記正規化**（`61%→0.61`、カンマ・通貨記号の除去、
  enum 文字列の表記揃えなど）。表記の変換は判断を伴わないため AI で問題ない。
- **Python に残す（明文化・非ブラックボックス）**: **判断を伴う業務ロジック**。
  EngagementLevel のセグメント判定（`_classify_engagement`）、Product 名寄せ（`_match_products`）など。
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

---

## 3. 3層アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│              Layer 3: Marketing Agent Layer               │
│                                                           │
│        MarketingAgent（単一・汎用 / ADK Agent + Tools）    │
│           入口: chat_stream（チャット, SSE ストリーム）     │
│  ─────────────────────────────────────────────────────── │
│  タスク（エージェントの種類ではなく「指示」で切り替わる）      │
│  ・メール起草（compose_emails, 実装済み）                   │
│  ・イベント振り返り分析（KPI/費用/アンケート参照, 実装済み）   │
│  ・戦略レポート保存（save_report, 実装済み）                 │
│  ・ハウスリストへのセグメント提案（将来）                    │
│  Tools: list_events / get_event_* / get_content_catalog / │
│         compose_emails / save_report / export_emails_csv …│
├──────────────────────────────────────────────────────────┤
│              Layer 2: Event Ontology                      │
│                                                           │
│  Event / EventKPI / SurveyResponse / CostItem /          │
│  Contact / EngagementLevel / ContentAsset / Product /    │
│  ComposedEmail / EmailBlock / DataLineage                │
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

## 4. オントロジーエンティティ

### コアエンティティ一覧

すべて `ontology.py` に定義（Pydantic モデル / Enum）。

#### Event 系（イベントを中心とした記録）

| エンティティ | 種別 | 説明 |
|---|---|---|
| `Event` | Model | 主催・管理するイベントの記録。会場・期間・予算・目標数を持つ |
| `EventType` | Enum | 展示会 / セミナー / プライベートイベント |
| `EventStatus` | Enum | 計画中 / 開催中 / 終了 |
| `EventKPI` | Model | イベントの実績指標（来場・獲得数・アポ数・開封/返信率・パイプライン・成約） |
| `EngagementCounts` | Model | 温度感別の獲得数内訳（`EventKPI` に内包） |
| `SurveyResponse` | Model | アンケート集計（NPS・満足度スコア・自由記述） |
| `SatisfactionScore` | Model | カテゴリ別満足度（`SurveyResponse` に内包） |
| `SatisfactionCategory` | Enum | ブースデザイン / 製品デモ / スタッフ対応 / コンテンツ品質 / 総合 |
| `CostItem` | Model | イベント費用の明細1件 |
| `CostCategory` | Enum | ブース出展料 / 装飾設営 / 機材 / 人件費 / 交通宿泊 / 印刷 / 飲食 / その他 |
| `CostSummary` | Model | 費用の合計・カテゴリ別集計（API レスポンス用の集計値） |

#### Contact 系（人とその段階）

| エンティティ | 種別 | 説明 |
|---|---|---|
| `Contact` | Model | ハウスリストの永続的な連絡先（人）。`source_event_id` で出会ったイベントを保持 |
| `ContactStage` | Enum | 顧客の段階（LEAD / MQL / SQL / CUSTOMER / EXCLUDED） |
| `EngagementLevel` | Enum | リード段階での温度感（アポ獲得済み / アポなし・感度高 / 通常リード） |
| `Product` | Enum | 自社プロダクト |

#### コンテンツ・メール

| エンティティ | 種別 | 説明 |
|---|---|---|
| `ContentAsset` | Model | 推薦可能なコンテンツ（資料・事例・開催予定セミナー等）。`linked_event_id` で `Event` と任意リンク |
| `ContentType` | Enum | 未来のセミナー / 未来のイベント / 資料・ホワイトペーパー / 導入事例 |
| `ComposedEmail` | Model | 構成判断を経て生成されたメール |
| `EmailBlock` | Model | メールの構成単位。包含根拠（`reason_for_inclusion`）を必須で持つ |

#### 来歴・加工処理レポート（Auditable AI / 原則4）

二段階処理の「何をどう変換したか」を記録する。詳細は原則4の実装例を参照。

| エンティティ | 種別 | 説明 |
|---|---|---|
| `DataLineage` | Model | バッチ単位の来歴記録。ステージ1出力とステージ2の加工根拠を束ねる |
| `ColumnMappingResult` | Model | パスA（`run_schema_mapper`）の出力: CSVカラム → フィールドのマッピング |
| `DocumentExtractionResult` | Model | パスB（`run_document_extractor`）の出力: 抽出エンティティ群 |
| `TransformDecision` | Model | ステージ2の1判定。`reason`（非null）で根拠を保持 |
| `EntityTransformation` | Model | 1エンティティ生成時の判定集合 |
| `SkippedRecord` | Model | エンティティ化されなかった入力とその理由 |
| `TransformationSummary` | Model | バッチ単位の加工サマリ |

### Contact と ContactStage の関係

```
Contact（ハウスリスト）
  ├── stage: ContactStage
  │     LEAD（展示会等で出会った段階）
  │       └── engagement: EngagementLevel  ← 温度感
  │             APPOINTMENT_BOOKED（アポ獲得済み）
  │             HIGH_INTENT（アポなし・感度高）
  │             NURTURING（通常リード）
  │     MQL（マーケティング活動で資格付け済み）
  │     SQL（営業に渡った）
  │     CUSTOMER（顧客化済み）
  │     EXCLUDED（対象外）
  └── source_event_id: Optional[str]   ← どのイベントで出会ったか（Event への参照）
```

- `Lead` は独立エンティティではなく、`ContactStage` の値のひとつ
- `EngagementLevel` は `stage = LEAD` のときに意味を持つ詳細情報
- 現在のMVPではすべてのコンタクトが `stage = LEAD`
- **出会ったイベントは `Contact.source_event_id`（Event への単純参照）で保持する。**
  接触記録を独立エンティティ（`EventInteraction`）にはしない — MVPでは「1コンタクト = 主に1つの出会いイベント」で十分であり、
  多対多の接触履歴を持ち込む前にまず中心軸（Event）と人（Contact）の結びつきを最小構成で確立する判断。

### Event と ContentAsset の関係（セミナーのデュアル性）

開催予定のセミナー/イベントは「主催・管理する対象」と「推薦するコンテンツ」の二面性を持つ。

```
Event（主催・管理）               ContentAsset（推薦・案内）
  ├── KPI / 費用 / 参加者            ├── url
  └── status: 開催済み/予定           ├── content_type: SEMINAR_UPCOMING
                                    └── linked_event_id: Optional[str]  ← リンク（任意）
```

| データ種別 | Event | ContentAsset |
|---|---|---|
| 開催済みイベント | ✓ | ✗ |
| 開催予定のセミナー/イベント | ✓（管理用） | ✓（推薦用） |
| ホワイトペーパー・事例 | ✗ | ✓ |

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

**MarketingAgent の構成**: Google ADK の `Agent`（`agents/marketing_agent.py` の `_agent`）として実装。

- 入口は `chat_stream`（チャット、Server-Sent Events でストリーミング）
- オントロジー参照・操作はすべて**ツール**として提供（`list_events` / `get_event_detail` /
  `get_event_contacts` / `get_event_kpi` / `get_event_survey` / `get_event_costs` /
  `get_all_events_summary` / `get_content_catalog` / `save_report` / `compose_emails` / `export_emails_csv`）
- メール生成は重いため非同期ラン化: `compose_emails` ツールが run を作成し、`_execute_email_run` が実行する

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
| `/api/marketing/runs/{id}/execute` | POST | `compose_emails` ツールが作成した run のメール生成を実行 |
| `/api/marketing/runs/{id}` | GET | メール生成ランの進捗取得 |
| `/api/marketing/runs/{id}/results` | GET | 生成されたメール一覧 |
| `/api/marketing/runs/{id}/export` | GET | 生成結果をCSVでエクスポート |

> **設計判断: run は POST で「作る」のではなく、ツールが作る。** 旧設計の `POST /api/marketing/runs`（ラン作成）は廃止。
> ユーザーはチャットでエージェントに指示し、エージェントが `compose_emails` ツールを呼んで run を作成する。
> フロントは返ってきた `run_id` に対して `/execute` を叩いて実行を起動する。
> 「何を生成するか」の判断をエージェント側に置き、API は実行・取得のみを担う分離。

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
| **コミュニケーション戦略** (Communication Strategy) | `_SYSTEM_PROMPT` 内のブロック選択ルール | `EngagementLevel` に基づくブロック構成ルール |
| **バッチ** (Batch) | `batch_id` | 1回のデータ統合セッション。**複数ファイルを束ねた1アップロード**（同一イベントに属するファイル群を横断処理する単位） |
| **ラン** (Run) | `run_id` | 1回のメール生成実行。`compose_emails` ツールが作成し `/execute` で起動 |
| **チャットセッション** (Chat Session) | `session_id` | `MarketingAgent` との対話セッション。ADK の `InMemorySessionService` で管理 |
| **コンテンツカタログ** (Content Catalog) | Firestore `content_assets` コレクション | 推薦可能なコンテンツ資産。`get_content_catalog` ツールが取得（DB化済み） |

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
- ❌ `Lead` を独立エンティティとして扱わない。`ContactStage.LEAD` が正しい
- ❌ `EventInteraction` を独立エンティティとして復活させない。出会ったイベントは `Contact.source_event_id` で保持する
- ❌ マーケティングアクションを「専用API」として増やさない。エージェントへの指示（チャット）＋ツールで表現する

---

## 7. 旧名→新名の対応表（移行メモ）

| 旧名 | 新名 | 種別 |
|---|---|---|
| `StructuredLead` | `Contact` | Pydanticモデル |
| `LeadSegment` | `EngagementLevel` | Enum |
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
| `EventInteraction` | （廃止）→ `Contact.source_event_id` | エンティティ |
| `POST /api/execute` / `POST /api/marketing/runs`（ラン作成） | （廃止）→ `compose_emails` ツール + `POST /api/marketing/runs/{id}/execute` | APIパス |
| `CONTENT_CATALOG`（静的定数） | （廃止）→ Firestore `content_assets` コレクション | データソース |
