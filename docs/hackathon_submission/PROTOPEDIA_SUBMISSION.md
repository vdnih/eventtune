# ProtoPedia 提出内容（コピペ用）— EventTune

> DevOps × AI Agent Hackathon（Findy / Google Cloud）用。
> 各セクションを ProtoPedia の対応フィールドにそのまま貼り付けてください。
> 文字数は上限の厳しいフィールドのみカウントを併記しています。

---

## 作品ステータス
**完成**

---

## 作品タイトル（上限50字）

**採用案A（39字・推奨）**
```
EventTune｜イベントの出会いを、根拠つきの個別フォローと振り返り分析へ
```

代替案B（32字・簡潔重視）
```
EventTune — イベントマーケティング・インテリジェンス
```

---

## 作品のURL
```
https://app.eventtune.link
```
> GitHub リポジトリURLは「関連リンク」に併記（本番URLと二重掲載可）。

---

## 概要（上限100字・SNS共有時に表示）

**採用案（89字）**
```
展示会後のバラバラなExcelを投げ込むだけで、AIエージェントが意味で統合。参加者一人ひとりへ根拠つきの個別フォローと、費用対効果の振り返り分析を、熱が冷めないうちに届けます。
```

代替案（70字・さらに短く）
```
展示会後の散らばったExcelを投げ込むだけ。AIエージェントが意味で統合し、根拠つきの個別フォローと費用対効果の振り返りを翌日に届けます。
```

---

## ライセンス
**表示する：Creative Commons Attribution CC BY 4.0**
> ハッカソン公開方針に沿って CC BY を選択。最終確定前にチームで合意を。

---

## システム構成（Markdown で入力 / 1枚目に構成図画像）

> `architecture.png`（`architecture.svg` から書き出し・880×495）を「システム構成」画像欄にアップロードし、本文は以下を貼り付け。

```markdown
EventTune は「カオスなイベントデータをオントロジーに統合し、AIエージェントがその上で働くマルチテナント SaaS」です。設計思想は一言で **「LLM の知能はプロンプトではなく"構造"で統治する」**。

1. **投入** — スペース（テナント）を作り、展示会の名簿・アンケート・ブース記録・費用の CSV/Excel をチャット画面にドラッグ投入。
2. **意味統合** — `DataIntegrationAgent`（Google ADK + Gemini on Vertex AI）が列名・形式のゆらぎを吸収し、**OSI セマンティックレイヤー（星座型オントロジー：Person / Account / Product / Content / Event の5マスタ＋ファクト）** へ統合。各エンティティに自然文要約 `appeal_summary` と埋め込み `appeal_vector`（gemini-embedding-001 / 768次元）を付与。
3. **分析** — `MarketingAgent`（ADK + Gemini）にチャットで質問。費用対効果や振り返りは **Vertex AI Agent Engine のサンドボックス上で AI が生成した Python を実データに対して実行**し、コードと結果の両方を提示（Code Interpreter）。「それっぽい数字」を答えさせない。
4. **個別フォロー生成** — **セグメント方式＋Human-in-the-Loop**：AI が切り口（軸）を設計 → 人が承認 → 分類 → バケット数 K 回だけパターン生成 → 各人への組み立ては**決定論 Python（LLM 呼び出しゼロ）**。LLM コストを O(N) から O(K) へ。各文面ブロックには採用理由 `reason_for_inclusion` が必ず残る（Auditable AI）。
5. **デプロイ / 運用（DevOps）** — Cloud Run（asia-northeast1）/ Firestore / Firebase Authentication・Storage・App Hosting。**Terraform ＋ GitHub Actions（Workload Identity Federation のキーレス認証）** で継続デプロイ。設計判断は 13 本の ADR に「何を採用し何を撤回したか」まで記録。

### 技術スタック
- フロント：Next.js 15（App Router / SSR）+ React 19 + TypeScript + Tailwind CSS
- バック：Python 3.12 + FastAPI + Pydantic v2（オントロジー = 型の単一真実源）+ pandas
- AI：Google ADK + Gemini（Vertex AI）+ gemini-embedding-001 + Agent Engine（Code Interpreter / Session）
- 基盤：Cloud Run / Firestore / Firebase / Cloud Storage / Artifact Registry / Cloud Build
- IaC・CI/CD：Terraform + Docker + GitHub Actions（WIF キーレス）+ Firebase App Hosting
```

---

## 開発素材（3文字以上・候補から選択）

```
Google Cloud Run, Vertex AI, Gemini, Google ADK, Firebase, Firestore, Cloud Storage, Artifact Registry, Cloud Build, Next.js, React, TypeScript, Tailwind CSS, FastAPI, Python, Pydantic, pandas, Terraform, Docker, GitHub Actions
```
> 候補にヒットしないものは「タグ」へ回す。**必須要件の Cloud Run・Vertex AI・Gemini・ADK は必ず入れる。**

---

## タグ（5個程度）

```
生成AI, Gemini, Vertex AI, Google ADK, AIエージェント, Cloud Run, RAG, Next.js, マーケティング
```

---

## ストーリー（Markdown で入力）

```markdown
## 展示会のあと、あなたの数日は「Excel 整形」に溶けていませんか

展示会やセミナーが終わると、手元に残るのは名刺の山と、毎回列がバラバラの Excel（来場者リスト・アンケート・ブースでの会話メモ・出展費用の請求書）。整形に数日かけ、「今回の費用対効果はどうだった？」には勘で答え、フォローは全員に同じ「ご来場ありがとうございました」テンプレ。かといって ChatGPT に 100 人分を貼り付けても、コピペは終わらず、存在しない製品機能をそれらしく書いてしまう。

EventTune は、この「イベント後の壁」をまるごと引き受けます。

## AI エージェントが中核 — 「必然性」

EventTune の主役は 2 つの自律エージェントです。`DataIntegrationAgent` はカオスなファイルを読み解き、意味を判断してオントロジーに統合します。`MarketingAgent` はチャットの指示から、分析なら Python を書いて実データ上で実行し、フォローなら顧客を「刺さる軸」で分けて全員分をチューニングします。

なぜ汎用チャットでは足りないのか。答えは機能の量ではなく構造にあります。設計思想は **「LLM の知能はプロンプトではなく"構造"で統治する」** ── 意味はセマンティックレイヤーに、権限は型に、判断根拠は非 null フィールドに、コストは決定論に押し込み、AI には AI にしかできない仕事だけを残す。

## 使い方は「投げ込んで、チャットするだけ」

ファイルをドラッグ投入 → チャットで「先月比で費用対効果を出して」「来場者を関心で分けてフォロー案を作って」と頼むだけ。AI が提案した施策は人が承認してから実行（Human-in-the-Loop）。すべての数字とすべての文面ブロックに「なぜこの数字か」「なぜこの人にこの内容か」が添えられます。

## 技術的こだわり（実装力）

- **① オントロジー接地** — 全入力を OSI セマンティックレイヤー（星座型：5マスタ＋ファクト）に正規化。AI は「CSV の列」ではなく「Person が Event に参加し Product に関心を持つ」という意味の上で推論し、イベント横断比較やマルチホップ分析が構造的に可能。
- **② コスト O(N)→O(K)** — セグメント方式。パターン生成はバケット数 K 回だけ、各人への組み立てはプレースホルダ置換の決定論 Python。500 人でも LLM を 500 回叩かない。
- **③ 型によるテナント分離** — AI ツールはスコープ済みクライアントを closure で受け取り、シグネチャから space_id を排除。AI が他テナントを名指しすることすら不可能で、アクセス制御が LLM の判断に依存しない。
- **④ Auditable AI** — `reason_for_inclusion` などの根拠フィールドを Optional にしない規約。数値分析は Agent Engine サンドボックスで AI 生成 Python を実行し、コードと結果を可視化（ADR-009）。
- **⑤ Static Core & Dynamic Context** — 自社の機能・本質価値（L1/L2）は固定し、顧客の悩み（L3）だけ AI が生成。捏造禁止・1メール1機能のガードレール＋承認ゲートで、パーソナライズとブランド一貫性を両立（ADR-007）。
- 意味検索は専用ベクトル DB を使わず、埋め込みの総当たりコサイン類似度で監査可能に（ADR-008）。

## つくる・まわす・とどける（DevOps）

Terraform で責務ごとに分割したインフラ、GitHub Actions ＋ Workload Identity Federation の**キーレス** CI/CD、Firebase App Hosting の git push 自動デプロイ、そして 13 本の ADR。ADR には Event 中心設計・固定課題ラベルなど**撤回した判断まで記録**しています。プロトタイプではなく、実運用を見据えたフルサイクルで作りました。

---

**イベントの熱が冷めないうちに、振り返りも、100人に100通りのフォローも届く。**
出会いを整え、成果へチューニングする。── EventTune
```

---

## 動画
YouTube 限定公開URL を貼付（台本は `DEMO_VIDEO_SCRIPT.md`）。
```
（撮影後にURLを記入）
```

---

## 画像（最大5枚 / 1枚目=メイン・推奨880×495）
`SCREENSHOT_SHOTLIST.md` の指示に沿って用意。

---

## メンバー登録
- チーム名：**（要記入）**
- メンバー：`ryotaro nishinoue @vdnih` ほか（**要ヒアリング：氏名・ProtoPedia ID**）
- 各メンバーの役割3つ（例）：`企画・プロダクト設計` / `バックエンド・AIエージェント` / `フロントエンド・インフラ`
> 規約上、企業所属者も「個人の私的活動」として参加（企業を代表しない）。

---

## 関連リンク
```
https://app.eventtune.link
（GitHub リポジトリURL：Public 化後に記入）
```
（任意）技術アピール文書・ADR を公開する場合はその URL も。

---

## 提出前チェックリスト
- [ ] タイトル案A/B のどちらかを選択
- [ ] 概要（89字案 or 70字案）を選択
- [ ] GitHub リポジトリを Public 化 → URL 反映（商標「EventTune」露出の最終確認込み）
- [ ] 本番URL `https://app.eventtune.link` が到達可能
- [ ] メイン画像＋補足スクショをアップロード
- [ ] デモ動画URLを記入
- [ ] チームメンバー全員を登録・各人の役割3つを記入
- [ ] 必須要件（Cloud Run / Vertex AI・Gemini・ADK）が開発素材とシステム構成の両方に明記
