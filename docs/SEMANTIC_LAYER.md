# セマンティックレイヤー — OSI v1.0 概念モデル

このドキュメントは、本プラットフォームのデータモデルの **概念的な正典** である。
「データをどう意味づけて持つか（WHAT/WHY）」を定義し、その正典は YAML
（[`backend/semantic/osi_event_marketing_v1.yml`](../backend/semantic/osi_event_marketing_v1.yml)）にある。

> **役割分担（ドキュメント体系）**
> - 本書（SEMANTIC_LAYER）= データモデルの概念。何を dataset/dimension/metric/relationship として持つか。
> - YAML（`osi_event_marketing_v1.yml`）= その概念の機械可読な正典（**設計の単一の思想源**）。
> - [`PHILOSOPHY_AND_NAMING.md`](PHILOSOPHY_AND_NAMING.md) = システム設計・命名の HOW（Pydantic・Firestore・命名規約）。
> - [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) = マーケ観点の WHY（CEP / Static Core 等）。
> - [`INGESTION_MAPPING.md`](INGESTION_MAPPING.md) = ファイルを本モデルへ取り込むプロセスの概念（HOW・取り込み）。
> - [`ADR.md` ADR-008](ADR.md) = OSI 採用の意思決定記録。

---

## 1. なぜセマンティックレイヤーか

旧データモデルには 2 つの構造的な問題があった。

1. **スキーマの多重定義**: 同じスキーマが Pydantic（`ontology.py`）／ AI プロンプト文 ／
   決定論マッパーの enum マップ ／ フロント TS の 4 箇所に手書きで散在し、変更時にずれた。
2. **フラットかつ Event 中心**: `Contact` が「個人＋企業＋興味製品＋参加イベント」を 1 エンティティに
   詰め込み、`業種 × イベント × 製品` のようなマルチホップ分析を構造的に表現できなかった。

そこで、業界標準の考え方である **OSI（Open Semantic Interchange）v1.0** に倣い、
データの「意味」を 1 つの YAML に概念モデルとして集約する。狙いは
**「裏側の保存構造（Firestore）を意識せず、AI が意味レイヤーの上でマルチホップ推論できる」** こと。

例:
> 「製造業（`industry_type`）の担当者で、イベントA（`event_id`）に参加（`action_type`）し、
> かつ製品B（`product_id`）に有効な興味（`interest_status`）を持つ人数は？」

このような問いを、AI は裏側の SQL/NoSQL 構造を意識せず、意味レイヤー上で組み立てられる。

---

## 2. OSI の 5 コアコンポーネント

YAML は次の 5 つだけで構成する（物理テーブル定義は持たない）。

| コンポーネント | 役割 |
|---|---|
| **datasets** | 実体（マスタ）と行動ログ（ファクト）の集合。各 dataset が dimensions / metrics / identifiers を持つ |
| **dimensions** | 切り口（業種・役職・アクション種別 等）。`categorical` / `time` / `text` / `vector`（意味ベクトル） |
| **metrics** | 集計指標（`count_distinct` / `sum` / `avg` / `ratio`）。`filter` で業務ルールを宣言 |
| **relationships** | dataset 間の知識グラフ（FK の等値、または `semantic_affinity` のベクトル類似度） |
| **context** | 各要素に付す **AI 向けの指示文**。プロンプトの原典になる |

---

## 3. 採用した設計判断

### 3.1 ファクト・コンステレーション（星座型）— Event-Centric の撤回

基底は **5 個のマスタ系 dataset** であり、いずれも対等。Event はその 1 つにすぎない。
**旧「原則1: Event-Centric（イベントが中心概念）」は本モデルで撤回**する（[ADR-008](ADR.md)）。

```
           ┌─────────┐   ┌─────────┐   ┌──────────┐
   マスタ →  │ accounts│   │ products│   │ contents │
           └────┬────┘   └────┬────┘   └────┬─────┘
                │             │              │
           ┌────┴────┐   ┌────┴─────┐        │
   マスタ →  │ persons │───│ events   │        │   ← 5 マスタ
           └────┬────┘   └────┬─────┘        │
                │  event_attendances │ product_interests
                └──── ファクトは用途ごとに分割（今後さらに増える前提）

   意味的近接（semantic_affinity）: persons.appeal_vector ⇄ {contents, products,
   events}.appeal_vector のコサイン類似度で「この人に合うもの」を引く（点線の関係）。
```

- **5 マスタ**: `persons` / `accounts` / `events` / `products` / `contents`
- **ファクト**: `event_attendances`（person×event）, `product_interests`（person×product）ほか。
  用途ごとに分割し、**今後さらに増える前提**でスキーマを拡張可能に保つ。
- **意味的近接**: `persons` / `events` / `contents` / `products` は `appeal_summary`（監査可能な
  要約テキスト）と `appeal_vector`（その埋め込み）を持ち、コサイン類似度で結びつく（§3.5）。

### 3.2 物理層と意味層の分離

物理保存先は SQL を使わず Firestore に置く。YAML では `table:`（SQL）の代わりに
**`physical: {collection, id}`** で Firestore コレクションを宣言する。
一方、`name` / `description` / `context` からは技術用語（`dim_` / `fact_` 等）を排除し、
AI には意味だけを見せる。

### 3.3 metrics / relationships はセマンティック宣言のみ

Firestore は SQL の JOIN や `count_distinct` を実行しない。よって metrics / relationships は
**「AI への意味の手がかり」** として宣言するにとどめ、実集計は決定論 Python・エージェントのツールが担う
（[`PHILOSOPHY_AND_NAMING.md` 原則4](PHILOSOPHY_AND_NAMING.md) の責務境界に従う）。

### 3.4 YAML は設計仕様書（手書き同期）

YAML はランタイムではロードしない（PyYAML 依存なし）。
`ontology.py`（Pydantic）と Firestore パスは YAML から **手で導出**し、
整合はレビューと（任意の）整合テストで担保する。

### 3.5 顧客の関心はベクトルで表現する（課題の第一級化は撤回）

当初は `challenges`（顧客課題=CEP）を第一級マスタ＋ `person/product/content_challenges` の
多対多ブリッジ群でモデル化したが、**これを撤回した**（[ADR-008](ADR.md)）。理由:

- ブリッジが増えすぎてデータモデルが肥大化する。
- 固定ラベルの課題では**パターンマッチングしかできず**、課題に収まらない興味・関心・文脈を表現できない。

代わりに、`persons` / `events` / `contents` / `products` に **`appeal_summary`（監査可能な要約
テキスト）と `appeal_vector`（その埋め込み）** を持たせる。

- **persons**: ヒアリング内容・名刺メモ・過去の行動ログ・興味製品から、その人の関心・悩み・文脈を
  要約 → 埋め込み。
- **offer 側（contents / products / events）**: 内容・提供価値・訴求ポイントを要約 → 埋め込み。

「この人に意味的に近いコンテンツ/製品/イベント」を **コサイン類似度**（`relationships` の
`semantic_affinity`）で引き当て、CEP 駆動の案内を行う。これは
[`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) の **L3=CEP（動的文脈）** を、固定ラベルでなく
連続的な意味空間として接地するものである。

- **計算方式**: Firestore のベクトルインデックスや `find_nearest` は使わない。スペース内候補を
  読み込み、**決定論 Python の総当たりコサイン**（[`backend/semantic_search.py`](../backend/semantic_search.py)）で
  ランク付けする。これは「集計は Firestore でなく決定論 Python」という §3.3 の方針と一致し、
  スペース毎に小規模なため O(N) で十分。将来スケール時はベクトルフィールドを温存したまま
  検索関数だけ差し替えればよい。
- **監査**: 埋め込み自体はブラックボックスだが、一致根拠は**双方の `appeal_summary`（人が読める
  テキスト）を並べて提示**することで説明可能にする（Auditable AI）。`appeal_vector` は
  `appeal_summary` から導出される派生フィールドで、AI には直接見せない。

---

## 4. dataset 一覧

| dataset | 種別 | Firestore | 役割 |
|---|---|---|---|
| `accounts` | マスタ | `accounts` | 企業・組織（業種・規模） |
| `persons` | マスタ | `persons` | 個人（ハウスリスト）。旧 Contact を正規化。**appeal_summary / appeal_vector を持つ** |
| `events` | マスタ | `events` | イベント。実績（KPI/NPS/費用）は metrics に畳み込み。**appeal_summary / appeal_vector を持つ** |
| `products` | マスタ | `products` | 自社製品。**appeal_summary / appeal_vector を持つ** |
| `contents` | マスタ | `contents` | マーケ素材（WP / 事例 / ウェビナーアーカイブ / 募集中セミナー）。**appeal_summary / appeal_vector を持つ** |
| `event_attendances` | ファクト | `event_attendances` | 申し込み・参加（person×event） |
| `product_interests` | ファクト | `product_interests` | 製品への興味・商談（person×product） |
| `segments` | 運用 | `segments` | **動的セグメント**（フィルタ定義: 軸・バケット・criteria） |
| `segment_snapshots` | 運用 | `segments/{sid}/snapshots` | **静的スナップショット**（施策時点で凍結したメンバーの版。複数保持） |
| `segment_assignments` | 運用 | `…/snapshots/{snap}/assignments` | スナップショット配下のメンバー（person→bucket→reason, reason 必須） |
| `deliverable_patterns` | 出力 | `segments/{sid}/patterns/{bucket}__{format}` | **雛形**（segment×bucket×format の生成テンプレート, 人手編集可） |
| `deliverables` | 出力 | `marketing_runs/{run_id}/deliverables` | **個別カスタマイズ成果物**（person 単位, format 付き, reason_for_inclusion 必須） |
| `marketing_runs` | 運用 | `marketing_runs` | 施策実行（使用 snapshot を参照） |
| `integration_jobs` | 稼働ログ | `integration_jobs` | 取り込みエージェントの処理ジョブ記録（旧 data_lineage, Auditable AI） |

> **セグメントは動的定義＋静的版**: `segments` はフィルタ定義（動的）。施策時点で『誰が対象か』を
> 確定したメンバーは `segment_snapshots`（版）に凍結し、`segment_assignments` はその snapshot 配下に持つ。
> 1 つの動的セグメントに複数の snapshot（施策実行ごとの版）が積み重なり、いずれも保持される。
> `marketing_run` は使用した snapshot を参照する。

> **成果物は format を持つ `deliverables`**: メールは個別カスタマイズ成果物の一形態にすぎない。
> トークスクリプト・提案資料等も同じ `deliverables`（`format` で種別）として person 単位で保存する。
> 各バケット×format の**雛形**は `deliverable_patterns`（人手編集可）。person↔segment は snapshot の
> assignment 経由で推移的に辿る。

> **来歴は各データに inline、稼働記録は `integration_jobs`**: 旧 `data_lineage` を独立した「来歴
> オブジェクト」ではなく、取り込みエージェントの**処理ジョブログ**として残す。個々のデータの出自は
> 各 master/fact レコードの `source_job_id` / `source_file_id`（inline フィールド）から逆引きする。

> **意味的近接（dataset ではなく関係）**: 「person の関心 → 合う content/product/event」は
> dataset/ブリッジではなく `relationships.semantic_affinity`（appeal_vector のコサイン類似度）で表す。
> 旧 `challenges` マスタと `person/product/content_challenges` ブリッジは撤回した（§3.5）。

> **旧 Contact の分解**: `Contact` →
> `persons`（個人）＋ `accounts`（企業）＋ `event_attendances`（参加イベント）＋
> `product_interests`（興味製品）。課題・関心は `persons.appeal_summary` / `appeal_vector` が担う。
> フラットな 1 実体を、マスタ＋ファクトの星座へ正規化した。

> **KPI / NPS / 費用の扱い**: 旧 `EventKPI` / `SurveyResponse` / `CostItem`（集計）は
> 独立 dataset にせず、`events` の **metrics**（`total_visitors` / `nps_score` / `total_cost` /
> `cost_per_acquisition` 等）として畳み込んだ。費用明細やアンケート自由記述が必要になった時点で
> `event_costs` / `survey_verbatims` を fact dataset として追加する。

---

## 5. 物理（Firestore）への落とし方

全コレクションは `spaces/{space_id}/` 配下に置く（テナント分離は `SpaceContext` の
`col/doc` 前置で構造的に担保。[原則6](PHILOSOPHY_AND_NAMING.md)）。

```
spaces/{space_id}/
  accounts/{account_id}
  persons/{person_id}                ← フラット（旧 events/{eid}/batches/{bid}/contacts/{cid} を廃止）
                                       appeal_summary(str) / appeal_vector(array<double>) を保持
  products/{product_id}              ← appeal_summary / appeal_vector を保持
  contents/{content_id}             ← appeal_summary / appeal_vector を保持
  event_attendances/{attendance_id}
  product_interests/{interest_id}
  events/{event_id}                 ← appeal_summary / appeal_vector を保持
  segments/{segment_id}                                          ← 動的定義
    segments/{segment_id}/snapshots/{snapshot_id}               ← 静的版（凍結メンバー）
      …/snapshots/{snapshot_id}/assignments/{person_id}         ← person→bucket→reason
    segments/{segment_id}/patterns/{bucket}__{format}            ← 雛形（format対応）
  marketing_runs/{run_id}                                        ← 施策実行（snapshot_id 参照）
    marketing_runs/{run_id}/deliverables/{deliverable_id}        ← 成果物（person単位, format付き）
  integration_jobs/{job_id}                                      ← 取り込み稼働ログ（旧 data_lineage）

  ※ 各 master/fact レコードは source_job_id（= job_id）/ source_file_id を inline 保持し、
    来歴は独立コレクションではなく各データから逆引きする。
```

`appeal_vector` は Firestore の **通常の `array<double>` フィールド**として持つ（`Vector` 型・
ベクトルインデックスは使わない。§3.5）。課題系コレクションは作らない。

`persons` をトップレベル化したことで、旧モデルの「幽霊ドキュメント（`batches/{bid}` 親が
未実体化でクエリに出ない）」問題は解消する。来歴は各 person の inline フィールド
`source_job_id`（= batch_id）/ `source_file_id` で持ち、取り込みジョブの詳細は `integration_jobs` から辿る。

---

## 6. 反映先（手書き同期の対象）

YAML を正典として、以下を手で同期する。`context:` がプロンプトの原典になる。

| 反映先 | 内容 |
|---|---|
| `backend/ontology.py` | dataset → Pydantic モデル、dimension/enum → フィールド、identifier → FK、appeal_summary/appeal_vector |
| `backend/semantic_search.py` | `embed_text` / `cosine` / `find_similar`（総当たり）/ `generate_appeal_summary` |
| `backend/agents/ontology_mapper.py` | 取り込み時の Account/Person/EventAttendance/ProductInterest 分解＋person の appeal 生成（取り込み概念は [`INGESTION_MAPPING.md`](INGESTION_MAPPING.md)） |
| `backend/agents/data_integration_agent.py` | 物理パス解決・抽出スキーマ・プロンプト（意味名は YAML 由来）・appeal 埋め込み |
| `backend/agents/marketing_agent.py` | ツール（appeal_vector の意味検索 `find_relevant_*_for_person`）と `_SYSTEM_PROMPT` のオントロジー記述 |
| `backend/routers/data.py` | 閲覧ビューの `VIEWS` レジストリ |
| フロント `EventDataPanel.tsx` | TS インターフェース |

---

## 関連ドキュメント

- [`backend/semantic/osi_event_marketing_v1.yml`](../backend/semantic/osi_event_marketing_v1.yml) — 概念モデルの正典（YAML）
- [`PHILOSOPHY_AND_NAMING.md`](PHILOSOPHY_AND_NAMING.md) — システム設計・命名（HOW）
- [`MARKETING_PHILOSOPHY.md`](MARKETING_PHILOSOPHY.md) — CEP / Static Core（マーケ WHY）
- [`INGESTION_MAPPING.md`](INGESTION_MAPPING.md) — ファイル → 本モデルへの取り込みプロセスの概念
- [`ADR.md` ADR-008](ADR.md) — OSI セマンティックレイヤー採用の決定記録
