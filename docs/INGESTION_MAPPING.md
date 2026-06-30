# 取り込みマッピング — ファイル → OSI オントロジーへの分解（概念設計）

このドキュメントは、アップロードされたファイルを **OSI セマンティックレイヤー
（[`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md)）** のエンティティへ取り込む**プロセスの概念的な正典**である。
「ファイルがどうやってオントロジー上のデータになるか（HOW・取り込み）」を定義する。

> **役割分担（ドキュメント体系）**
> - 本書（INGESTION_MAPPING）= 取り込みプロセスの概念。ファイルをどう分解・リンク・永続化するか。
> - [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md) = 取り込み先のデータモデル（WHAT）。5マスタ＋ファクト＋appeal。
> - [`PHILOSOPHY_AND_NAMING.md`](PHILOSOPHY_AND_NAMING.md) = システム設計・命名・責務境界（HOW）。
> - [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) = CEP / HIL（マーケ WHY）。
> - [`ADR.md` ADR-008](ADR.md) = OSI 採用 / Event-Centric 撤回の意思決定。本書はその「第2バッチ（取り込み再実装）」の概念フェーズ。
> - [`ADR.md` ADR-011](ADR.md) = 取り込みの**依存順序化（観測→確定→結合→導出）**と**同一性の実在照合化（UUID＋検索 find-or-create、stable_id 全廃）**。§1.5・§2・§3 は本 ADR で改稿済み。

> **改訂（ADR-011）**: 取り込みには**依存順序**があり、マスタは観測から導出される**派生ディメンション**である、という
> 概念（§1.5）に基づき、パイプライン（§2）とリンク解決（§3）を**依存順の多段＋実在マスタへの検索**へ改めた。
>
> **改訂（ADR-013）**: AI Extract フェーズを2段構えに再設計。取り込み時の業務的判定（感度分類等）を廃止し、
> 観測事実をそのまま `event_attendances.challenge_note` に保存してベクトル検索に乗せる方針に変更。
> `DocumentPlan`（バッチ横断理解の出力）を導入し、各ファイルの業務文脈をCSV行抽出の文脈として活用する。

---

## 1. なぜ取り込みの再設計か

旧モデルは **Event-Centric** であり、取り込みも「1 ファイル = 1 イベントに属する」前提で組まれていた。
ユーザーがプルダウンで **ファイル → イベント** を事前割り当てし、`event_id` を取り込み全体の
**経路キー（root）** として `suggest-event` / `file_event_map` / `process_batch(event_ids)` /
`ontology_mapper` に通していた。

ADR-008 で **Event-Centric は撤回**され、基底は **5 個の対等なマスタ**になった。
よって「イベント割り当て」という操作自体が概念的に成立しない。取り込みは
**「ファイルというレコードの容れ物を、含まれるエンティティへ分解する」** ことに再定義される。

> **原則: ファイルはレコードの容れ物であり、イベントの所属物ではない。**
> Event は 5 マスタの 1 つにすぎず、取り込みの経路キーではない。
> 「この参加者行はどのイベントか」は、データ自身・文脈・チャットヒントから解決する
> **リンク（FK）** であって、人間の事前割り当てではない。

---

## 1.5 概念モデル: 参照の向き・発見の向き・観測・派生ディメンション

取り込み再設計の土台となる概念（ADR-011）。これを押さえると「実務の作業順」と「データモデル」の
ズレが概念の欠落から来ていたと分かる。

- **参照の向き（FK）と 発見の向きは逆**。`event_attendance → person/event` という FK は「マスタが先」に
  見える。しかしこの業務で **Person マスタは入力として与えられない**。与えられるのは「イベントで会った
  記録（参加者行）」であり、Person はその記録を重複排除して**導出する派生ディメンション**である
  （DWH の late-arriving / inferred dimension）。つまりマスタはファクト側の観測から発見される。
- **観測(observation)＝接客(encounter)が源泉**。参加者リスト1行の自然な粒度は person ではなく
  「(person, event) の接客1件」。この観測こそが `event_attendances`（ファクト）の源泉で、各回の
  接客担当・課題感・メモという**事実**を持つ。person はこの観測群を名寄せして初めて1人になる。
- **観測は『行ブロック』として一過性に扱う**。列分解の前に、各行を `{元列: 値}` の **JSON object**
  としてロスレスに捕捉する（表は1行=1観測、文書はAI抽出単位／全体）。これは取り込みプロセス内の
  一時表現で、**永続コレクションは作らず OSI の構成要素にも足さない**（§6 注の方針を踏襲）。
- **マスタは『conform される』もの**。persons / accounts は観測から、events / products は専用ファイル
  からも観測の参照名からも、いずれも「実在エンティティへの検索 find-or-create」で確定する（§3）。

## 2. 取り込みパイプライン（観測 → 確定 → 結合 → 導出）

取り込みは**バッチ横断の多段**であり、依存順（マスタ確定 → ファクト結合 → person 集約）を内部に持つ。
各ファイルは複数のエンティティ種別を同時に含み得る。ファイルの到着順は依存順と無関係なので、
per-file 独立処理ではなく、観測を集めてから段階的に解決する。

| # | ステージ | 担い手 | 内容 |
|---|---|---|---|
| 1 | **Load** | Python | 全ファイルを読み込む（CSV/TXT 問わず bytes → テキスト変換） |
| 2a | **AI Extract Step1（バッチ横断）** | AI フルモデル × 1回 | バッチ内全ファイルのヘッダー＋サンプル＋オントロジー定義を渡し、各ファイルの `DocumentPlan`（業務文脈・エンティティ種別・カラムマッピング・リンクヒント）を生成 |
| 2b | **AI Extract Step2（TXTのみ）** | AI フルモデル × TXTファイル数 | 非構造化テキストを `DocumentExtractor` で直接エンティティ抽出（変更なし） |
| 2c | **AI Extract Step3（CSVのみ・行単位並列）** | AI 軽量モデル × 行数（並列） | `DocumentPlan` を文脈として各行を `asyncio.gather` で並列処理 → `PersonObservation` を生成 |
| 3 | **確定 (Conform)** | Python | 観測＋専用マスタファイルの自然キーを横断し、**実在マスタへの検索 find-or-create**（§3）で events / accounts / persons / products を確定・永続。マスタの `appeal_summary` → `appeal_vector` もここで生成。 |
| 4 | **結合 (Bind)** | Python | `event_attendances`（接客担当/課題感/メモ付き）/ `product_interests` を、確定済みマスタの UUID へ束ねて永続。リンクスタブは作らない。 |
| 5 | **導出 (Derive)** | AI | 各 person の `appeal_summary` / `appeal_vector` を、その人の全 `event_attendances` ＋興味製品から集約生成して書き戻す。`source_job_id` を各レコードに inline 付与し、`integration_jobs` に稼働記録を残す。 |

**CSVパスの行単位並列化の理由**:
- 全行一括送信: トークン消費が大きく（行数×列数）、レスポンスも巨大になる
- 行単位並列: 1行=数十〜百トークン程度で軽量モデルが使える。`DocumentPlan` が業務文脈（「この行はどのイベントの参加者か」等）を提供するため、行単体でも十分な抽出が可能

> ステージ 3–5 は依存順に走る（確定→結合→導出）。UUID 主キーのため名前→ID の計算ショートカットが無く、
> 参照は必ず確定済みマスタへの検索で解決される＝この順序が構造的に強制される。

### Entity Classification の例

| ファイルの性質 | 分解先エンティティ |
|---|---|
| 参加者リスト CSV | `persons` ＋ `accounts` ＋ `event_attendances`（＋ event へのリンク） |
| イベント概要ドキュメント | `events`（KPI / NPS / 費用は events の **metrics** に畳む） |
| 製品マスタ | `products` |
| マーケ素材一覧（WP / 事例 / 募集中セミナー等） | `contents` |
| 製品への興味・商談ログ | `product_interests`（＋ person / product へのリンク） |

---

## 3. 同一性・リンク解決 — 実在マスタへの検索 find-or-create（UUID）

**全エンティティは UUID 主キー**であり、ID は名前から計算しない（`stable_id` 方式は全廃、ADR-011）。
**重複排除と参照解決は同じ機構**＝「スペース内の実在エンティティを **natural key で検索**し、ヒットすれば
その UUID を再利用、無ければ新規採番」で行う（確定フェーズ §2-3）。

**natural key と照合**:
- events = イベント名 / accounts = 会社名 / products = 製品名 / persons = email（無ければ氏名×会社）。
- 照合は `_normalize_name`（NFKC ＋ 全空白除去 ＋ lower）で正規化して比較。完全一致で外れたら
  **曖昧一致（最近傍／包含）でフォールバック**し、解決結果と根拠を `integration_jobs` に残す。

**リンク先“名”を得るためのシグナル優先順位**（どの名前で検索するかを決める。上位で確定したら下位は不要）:
1. **データ自身の列** — 行ごとに異なるイベント／会社／製品を識別する列（「イベント名」「会社名」「製品名」）。
2. **アップロード時のイベント明示紐付け／チャットヒント** — ユーザーの補足・上書き（§4。`event` パラメータが最優先の明示シグナル）。
3. **単一イベントバッチのフォールバック（イベントのみ）** — 行にも明示にもイベントリンクが無く、かつ
   そのバッチが確定したイベントが**ちょうど1つ**のとき、リンク未指定の観測をそのイベントへ束ねる。
   参加者リストを当該イベントの概要／アンケートと同じバッチで取り込む実務動線を救うための決定論的既定。
   バッチに複数イベントがある／イベントが0件のときはフォールバックしない（曖昧なら束ねない）。

得られた“名”で上記の検索 find-or-create を実行する。**リンクスタブ（仮メンバーの先行生成）は作らない**。
確定フェーズでマスタを materialize してから、結合フェーズで実在マスタへ束ねる（依存順）。

> **未解決リンクの扱い**（実装で確定済み）: リンク“名”があるが既存マスタに一致しない → find-or-create で
> 新規マスタを採番（観測からの発見）。イベントリンクが全く得られない（列・明示・単一イベントいずれも無い）
> → その参加ファクトは作らずスキップ（person 自体は作る）。「黙ってスタブを作って JOIN を割る」挙動は廃止。

---

## 4. チャットヒントの役割（既定は自動、ヒントは任意の補正レイヤー）

既定は **自動検出**。チャットヒントは **任意の曖昧解消・文脈・スコープ指定**であり、
**事前割り当てではない**。次の場面で効く:

- リンクが曖昧／候補が複数（「このバッチは 2025 秋展示会の参加者」）
- ユーザーがエンティティ種別やスコープを上書きしたい（「これは製品マスタで連絡先ではない」）

これに伴う UX/API の転換:

- 旧 `suggest-event`（**イベント決定を強制**）→ **「取り込みプラン提案」**（検出したエンティティ群と
  リンク案）へ置換。ユーザーはチャットで修正する。
- UI のプルダウン → **チャットヒント入力 ＋ 分解結果プレビュー**（何を何に分解・リンクしたかの可視化）。

これは [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) の **HIL（Human-in-the-Loop）** と
個別対応の自律オーケストレーション思想（PM 再設計）と一貫する。

---

## 5. AI / Python 責務境界

[`PHILOSOPHY_AND_NAMING.md` 原則4](PHILOSOPHY_AND_NAMING.md) /
[`SEMANTIC_LAYER.md` §3.3](SEMANTIC_LAYER.md) の責務境界に従う。

| 担い手 | 役割 |
|---|---|
| **AI（フルモデル）** | バッチ横断のファイル理解（`DocumentPlan` 生成）・非構造化テキスト抽出・`appeal_summary` 生成・チャットヒント解釈。 |
| **AI（軽量モデル・並列）** | CSV 行単位の観測事実抽出（`PersonObservation` 生成）。文脈は `DocumentPlan` から受け取る。 |
| **Python** | Firestore 読み書き・UUID 採番・find-or-create（業務的判定はしない）。 |

**取り込み時に業務的判定をしない原則（ADR-013）**:
- `appeal_vector` によるコサイン類似度で動的判断できるため、感度の事前分類が不要
- 感度・興味度の「観測事実」（「感度A」「関心高め」等のテキスト）は `challenge_note` にそのまま保存
- 業務的な分類・判定が必要な場合は、取り込み後に「セグメント作成」としてユーザーが実施する

`appeal_vector` の生成（埋め込み）と類似度は `semantic_search.py`（未実装）が担う。
取り込みの Appeal Generation ステージはこれに依存するため、**段階的に有効化**する
（マスタ・ファクトの分解と永続化を先に成立させ、appeal は後続で接続）。

---

## 6. 撤去されるもの（イベント中心の経路）

| 撤去対象 | 置換 |
|---|---|
| `suggest-event` エンドポイント | 一般「取り込みプラン提案」 |
| `file_event_map`（file → event[]） | イベント鍵のマップを廃止。per-file 取り込みプラン＋チャットヒント文脈 |
| `process_batch` / `ontology_mapper` を貫く `event_id` 経路キー | 実在マスタへの検索による Link Resolution（§3） |
| UI の割り当てプルダウン | チャットヒント入力＋分解結果プレビュー |
| `stable_id`（名前ハッシュ主キー）/ `stable_event_id` 等 | 全エンティティ UUID 主キー ＋ 検索 find-or-create（ADR-011, §3） |
| `_write_link_stubs`（リンクスタブの先行生成） | 確定フェーズで materialize → 結合フェーズで実在へ束ねる（依存順） |
| per-file 完結の分解・永続（並列・順序非依存） | バッチ横断の多段（観測→確定→結合→導出, §2） |
| `Person.notes`（行→Person への接客メモ集約） | `event_attendances` の owner_staff/challenge_note/memo（接客の観測事実） |

> **注**: 取り込みは「プロセス」であり OSI の 5 コンポーネント
> （datasets / dimensions / metrics / relationships / context）に属さない。
> よって YAML（`osi_event_marketing_v1.yml`）には足さず、本 markdown を概念の正典とする。

---

## 7. 実装フェーズ（ADR-008 第2バッチ — 実装済み）

> **注（ADR-011）**: 以下は ADR-008 第2バッチとして実装済みだが、その取り込み層は「per-file 完結＋
> `stable_id`＋リンクスタブ」で組まれており、ADR-011 の**多段（観測→確定→結合→導出）＋ UUID＋検索
> find-or-create**へ作り直す（§1.5・§2・§3）。本節は履歴として残す。ADR-011 の実装は本書のレビューゲート後。

本書のレビューゲートを経て、以下はすべて実装済み:

- ✅ `semantic_search.py` 新設（埋め込み・総当たりコサイン・`generate_appeal_summary`）。
  さらに消費側（`find_similar` を使う `find_relevant_for_person` ツール／segmentation の意味的
  近接分類）も配線済み（ADR-010）。
- ✅ `suggest-event` / `file_event_map` の撤去
- ✅ `process_batch` / `ontology_mapper` / `data_integration_agent` を per-entity 分解＋
  Link Resolution へ再設計
- ✅ フロント `UploadConfirmModal` をチャットヒント＋分解プレビューへ作り替え（取り込みは
  `POST /api/integration/plan` → 確認 → `POST /api/integration/batches`）

---

## 関連ドキュメント

- [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md) — 取り込み先のデータモデル（WHAT）
- [`PHILOSOPHY_AND_NAMING.md`](PHILOSOPHY_AND_NAMING.md) — 責務境界・命名（HOW）
- [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) — CEP / HIL（WHY）
- [`ADR.md` ADR-008](ADR.md) — OSI 採用 / Event-Centric 撤回の決定記録
