# ソフトウェアアーキテクチャ

## コア設計原則: Pydantic オントロジー（Single Source of Truth）

`backend/ontology.py` がシステム全体の型定義の唯一の真実源。
エージェントはすべてこの型に従ってデータの入出力を行う。

```python
# backend/ontology.py — 全モデルの定義場所

class ProductSegment(str, Enum):
    PRODUCT_A = "プロダクトA"
    PRODUCT_B = "プロダクトB"

class ContentType(str, Enum):
    SEMINAR_UPCOMING = "未来のセミナー（募集中）"
    EVENT_UPCOMING   = "未来のイベント（募集中）"
    WHITE_PAPER      = "資料・ホワイトペーパー"
    CASE_STUDY       = "導入事例"

class LeadSegment(str, Enum):
    APPOINTMENT_BOOKED = "アポ獲得済み"
    HIGH_INTENT        = "アポなし・感度高"
    NURTURING          = "通常リード"

class BlockType(str, Enum):
    GREETING              = "1_展示会のお礼と挨拶"
    SCHEDULE_PROPOSAL     = "2_日程調整・候補日打診"
    CASE_STUDY_INTRO      = "3_導入事例の紹介"
    PRODUCT_MATERIAL_INTRO = "4_プロダクト資料・ホワイトペーパーの紹介"
    SEMINAR_INTRO         = "5_未来の募集中のセミナー案内"
    CLOSING               = "6_結びの挨拶"

class StructuredLead(BaseModel):
    name: str
    company_name: str
    department: str
    job_title: str
    segment: LeadSegment
    interested_products: List[ProductSegment]
    extracted_challenge: str

class EmailBlock(BaseModel):
    block_type: BlockType
    reason_for_inclusion: str   # Chain-of-Thought: 必須フィールド
    associated_content_ids: List[str] = []
    block_text: str

class TotalTailoredEmail(BaseModel):
    subject: str
    email_blocks: List[EmailBlock]

class ContentItem(BaseModel):       # コンテンツライブラリ用
    id: str
    content_type: ContentType
    name: str
    description: str
    url: str
```

---

## エージェント構成

### 全体フロー

```
ファイルアップロード
      │
      ▼
POST /api/ingest
      │
      ▼  (BackgroundTask)
Ingestion Agent
  - Gemini 2.5 Flash + response_schema=_IngestionResponse
  - 15行バッチで処理
  - CSV列名を正規化・セグメント判定・製品マッピング
  - StructuredLead[] → Firestore batches/{id}/leads/
      │
      ▼  (取り込み完了後、UIからトリガー)
POST /api/execute
      │
      ▼  (BackgroundTask)
Execution Agent
  - Gemini 2.5 Flash + response_schema=TotalTailoredEmail
  - 1リード1コール（2秒インターバル、429時は指数バックオフ）
  - コンテンツライブラリをプロンプトに注入
  - TotalTailoredEmail → Firestore emails/
```

### Ingestion Agent (`agents/ingestion_agent.py`)

| 項目 | 内容 |
|------|------|
| モデル | `gemini-2.5-flash` |
| 入力 | `pd.DataFrame`（列名不統一・表記ゆれあり） |
| 出力 | `List[StructuredLead]` + Firestore保存 |
| バッチサイズ | 15行/コール |
| Structured Output | `response_schema=_IngestionResponse`（`leads: list[StructuredLead]`） |

**セグメント判定ロジック（プロンプト定義）**

| 判定シグナル | LeadSegment |
|------------|-------------|
| 判定=A + 温度感=高、またはメモに「面談希望」「アポ」 | `APPOINTMENT_BOOKED` |
| 判定=B または 温度感=中〜高、資料請求・見積依頼 | `HIGH_INTENT` |
| 判定=C、名刺交換のみ、温度感=低 | `NURTURING` |

**製品マッピングロジック（プロンプト定義）**

| キーワード | ProductSegment |
|-----------|---------------|
| スキルマップ、技能伝承、アーカイブ、育成 | `PRODUCT_A` |
| 要員配置、シフト、シミュレーター、資格管理、安全講習 | `PRODUCT_B` |

### Execution Agent (`agents/execution_agent.py`)

| 項目 | 内容 |
|------|------|
| モデル | `gemini-2.5-flash` |
| 入力 | `StructuredLead`（Firestoreから取得） |
| 出力 | `TotalTailoredEmail` + Firestore保存 |
| CoT強制 | `reason_for_inclusion` は Pydantic `required` フィールド |
| レート制御 | コール間2秒インターバル、429時は指数バックオフ（10s/20s/40s/80s、最大4回） |

**ブロック選択ルール（セグメント別）**

| BlockType | APPOINTMENT_BOOKED | HIGH_INTENT | NURTURING |
|-----------|:-----------------:|:-----------:|:---------:|
| 展示会のお礼と挨拶 | 必須 | 必須 | 必須 |
| 日程調整・候補日打診 | 必須 | 任意 | 含めない |
| 導入事例の紹介 | 必須 | 必須 | 任意 |
| プロダクト資料の紹介 | 任意 | 必須 | 必須 |
| セミナー案内 | 任意 | 任意 | 必須 |
| 結びの挨拶 | 必須 | 必須 | 必須 |

---

## コンテンツライブラリ (`contents_library.py`)

`backend/contents_library.py` に15件の `ContentItem` を定義。`ContentType` へのマッピング：

| id | ContentType |
|----|------------|
| seminar_01〜05 | `SEMINAR_UPCOMING` |
| event_01〜05 | `EVENT_UPCOMING` |
| doc_01, doc_02, doc_04, doc_05 | `WHITE_PAPER` |
| doc_03（成功事例集） | `CASE_STUDY` |

Execution Agent はこのライブラリをJSON形式でプロンプトに注入し、`EmailBlock.associated_content_ids` で参照IDを返す。

---

## FastAPI ルート一覧

すべてのルートで `Authorization: Bearer {firebase_id_token}` が必須。

```
# Ingestion（取り込みパイプライン）
POST   /api/ingest                          # CSV/Excelアップロード、Ingestion Agent起動
GET    /api/batches/{batchId}               # バッチ状態・セグメント内訳
GET    /api/batches/{batchId}/leads         # 取り込み済みリード一覧

# Execution（メール生成パイプライン）
POST   /api/execute                         # メール生成ジョブ起動 {batch_id}
GET    /api/execute/{batchId}/status        # 進捗確認（ポーリング用）
GET    /api/execute/{batchId}/emails        # 生成済みメール一覧
GET    /api/execute/{batchId}/download      # CSV一括ダウンロード

# 旧エンドポイント（互換維持）
POST   /api/generate                        # CSVアップロード → メッセージ1行生成
GET    /api/jobs/{jobId}                    # ジョブ状態確認
GET    /api/jobs/{jobId}/download           # CSV出力

# ヘルスチェック
GET    /health
```

---

## Firestore データモデル

### `batches/{batchId}`

```json
{
  "filename": "manufacturing_dx_event_leads.csv",
  "row_count": 40,
  "status": "ingesting | done | error",
  "lead_count": 40,
  "created_at": "2026-06-12T00:00:00+00:00",
  "execution_status": "running | done | error",
  "execution_done": 40,
  "email_count": 40,
  "error": null
}
```

### `batches/{batchId}/leads/{leadId}`

```json
{
  "lead_id": "uuid",
  "name": "田中 修一",
  "company_name": "株式会社山田製作所",
  "department": "生産技術部",
  "job_title": "工場長",
  "segment": "アポ獲得済み",
  "interested_products": ["プロダクトA"],
  "extracted_challenge": "熟練工の退職前に技能を動画でアーカイブしたい"
}
```

### `emails/{emailId}`

```json
{
  "email_id": "uuid",
  "lead_id": "uuid",
  "batch_id": "uuid",
  "subject": "【山田製作所様】技能伝承デジタル化のご提案",
  "blocks": [
    {
      "block_type": "1_展示会のお礼と挨拶",
      "reason_for_inclusion": "アポ獲得済みリードへの冒頭挨拶として必須。展示会での商談を振り返りつつ感謝を伝える。",
      "associated_content_ids": [],
      "block_text": "先日はスマート工場EXPOにてお時間をいただき..."
    }
  ],
  "created_at": "2026-06-12T00:00:00+00:00"
}
```

---

## ディレクトリ構成（詳細）

```
marketing-mail-generator/
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                           # → /dashboard にリダイレクト
│   │   ├── globals.css
│   │   ├── (auth)/
│   │   │   └── login/page.tsx                 # Google OAuth ログイン
│   │   └── (app)/
│   │       ├── layout.tsx                     # Firebase 認証ガード
│   │       └── dashboard/page.tsx             # チャット駆動メインUI
│   ├── components/
│   │   └── features/
│   │       ├── upload/FileDropzone.tsx         # CSV/Excel ドロップ
│   │       └── email/EmailBlockCard.tsx        # メールブロック表示 + CoT トグル
│   └── lib/
│       ├── firebase.ts
│       └── utils.ts
│
└── backend/
    ├── ontology.py                             # Pydantic SSoT（変更禁止原則）
    ├── contents_library.py                     # ContentItem×15件
    ├── agents/
    │   ├── ingestion_agent.py                  # Ingestion Agent
    │   ├── execution_agent.py                  # Execution Agent
    │   └── message_generator.py               # 旧エージェント（互換維持）
    ├── routers/
    │   ├── ingest.py
    │   ├── execute.py
    │   └── generate.py                        # 旧エンドポイント（互換維持）
    ├── main.py
    ├── config.py
    ├── dependencies.py                        # Firebase ID token 検証
    ├── jobs.py                                # 旧ジョブ管理（互換維持）
    └── pyproject.toml
```

---

## 主要フロントエンドコンポーネント

### `dashboard/page.tsx`

チャット駆動のメインUI。状態遷移:

```
idle → (ファイルドロップ) → ingesting → ingested
                                            │
                                     (「メールを生成して」)
                                            ↓
                                       executing → executed
```

チャットコマンドのディスパッチ:

| 入力パターン | 動作 |
|------------|------|
| 「メール」「生成」「作成」 | `POST /api/execute` → ポーリング → EmailBlockCard表示 |
| 「リード」「確認」「一覧」 | `GET /api/batches/{id}/leads` → チャットに一覧表示 |
| 「ダウンロード」「CSV」 | `GET /api/execute/{id}/download` |
| その他 | ヘルプメッセージ表示 |

### `EmailBlockCard`

- メール1件をブロック単位で折り畳み表示
- 「AIの思考を見る」ボタンで `reason_for_inclusion`（CoT）をトグル
- `associated_content_ids` をバッジ表示
- セグメント別カラーバッジ

### `FileDropzone`

- `.csv` / `.xlsx` / `.xls` 対応
- ファイル選択と同時に `POST /api/ingest` を自動実行（ボタン不要）
