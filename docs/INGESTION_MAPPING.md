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

## 2. 取り込みパイプライン（概念ステージ）

取り込みは次の 7 ステージからなる。各ファイルは**複数のエンティティ種別を同時に含み得る**。

| # | ステージ | 内容 |
|---|---|---|
| 1 | **Read & Profile** | ファイル種別（表形式 / ドキュメント）を判定し、列・先頭プレビューを把握する。 |
| 2 | **Entity Classification** | このファイルが含む**エンティティ種別を判定（複数可）**。下表参照。 |
| 3 | **Field Mapping** | 列 → オントロジーフィールドへ写像（`column_map`）。検出した種別ごとに行う。 |
| 4 | **Link Resolution** | FK（誰がどのイベントに参加／どの企業所属／どの製品への興味）を解決（§3）。 |
| 5 | **Decompose & Dedup** | エンティティを生成。安定 ID で重複排除し、`source_job_id` / `source_file_id` を inline 付与。 |
| 6 | **Appeal Generation** | persons 等の `appeal_summary` → `appeal_vector` を生成（`semantic_search.py` に依存。§5）。 |
| 7 | **Persist & Job Log** | `spaces/{space_id}/...` フラットコレクションへ保存し、`integration_jobs` に稼働記録を残す。 |

### Entity Classification の例

| ファイルの性質 | 分解先エンティティ |
|---|---|
| 参加者リスト CSV | `persons` ＋ `accounts` ＋ `event_attendances`（＋ event へのリンク） |
| イベント概要ドキュメント | `events`（KPI / NPS / 費用は events の **metrics** に畳む） |
| 製品マスタ | `products` |
| マーケ素材一覧（WP / 事例 / 募集中セミナー等） | `contents` |
| 製品への興味・商談ログ | `product_interests`（＋ person / product へのリンク） |

---

## 3. Link Resolution — リンクの解決源と優先順位

リンク（FK）は次の優先順位で解決する。**上位で確定したら下位は使わない。**

1. **データ自身の列** — 行ごとに異なるイベント／会社／製品を識別する列
   （例「イベント名」「会社名」「製品名」）。旧 `event_routing_column` を**イベント以外にも一般化**する。
2. **ファイルレベル文脈** — ファイル名・ドキュメント本文から推定（ファイル全体が単一イベント等）。
3. **チャットヒント** — ユーザーの自然言語による補足・上書き（§4）。
4. **既存マスタとの名寄せ** — name → 安定 ID の **find-or-create**（決定論）。同名は新規採番せず統合。

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
| **AI（意味を変えない判断）** | エンティティ種別分類・列→フィールドの意味マッピング・曖昧テキストからのリンク推論・`appeal_summary` 生成・チャットヒント解釈。 |
| **決定論 Python（業務ロジック・明文化）** | 安定 ID の find-or-create（名寄せ・重複排除）・名前一致後の FK 確定・スキーマ検証・永続化・ジョブログ。 |

`appeal_vector` の生成（埋め込み）と類似度は `semantic_search.py`（未実装）が担う。
取り込みの Appeal Generation ステージはこれに依存するため、**段階的に有効化**する
（マスタ・ファクトの分解と永続化を先に成立させ、appeal は後続で接続）。

---

## 6. 撤去されるもの（イベント中心の経路）

| 撤去対象 | 置換 |
|---|---|
| `suggest-event` エンドポイント | 一般「取り込みプラン提案」 |
| `file_event_map`（file → event[]） | イベント鍵のマップを廃止。per-file 取り込みプラン＋チャットヒント文脈 |
| `process_batch` / `ontology_mapper` を貫く `event_id` 経路キー | per-entity の Link Resolution |
| UI の割り当てプルダウン | チャットヒント入力＋分解結果プレビュー |

> **注**: 取り込みは「プロセス」であり OSI の 5 コンポーネント
> （datasets / dimensions / metrics / relationships / context）に属さない。
> よって YAML（`osi_event_marketing_v1.yml`）には足さず、本 markdown を概念の正典とする。

---

## 7. 実装フェーズ（本概念の承認後）

本書はレビューゲート。承認後に ADR-008 第2バッチの取り込み実装へ進む:

- `semantic_search.py` 新設（埋め込み・総当たりコサイン・`generate_appeal_summary`）
- `suggest-event` / `file_event_map` の撤去
- `process_batch` / `ontology_mapper` / `data_integration_agent` を per-entity 分解＋Link Resolution へ再設計
- フロント `UploadConfirmModal` をチャットヒント＋分解プレビューへ作り替え

---

## 関連ドキュメント

- [`SEMANTIC_LAYER.md`](SEMANTIC_LAYER.md) — 取り込み先のデータモデル（WHAT）
- [`PHILOSOPHY_AND_NAMING.md`](PHILOSOPHY_AND_NAMING.md) — 責務境界・命名（HOW）
- [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) — CEP / HIL（WHY）
- [`ADR.md` ADR-008](ADR.md) — OSI 採用 / Event-Centric 撤回の決定記録
