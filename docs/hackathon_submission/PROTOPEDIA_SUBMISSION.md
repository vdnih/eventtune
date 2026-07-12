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

代替案B（簡潔重視）
```
イベントマーケAIエージェント「EventTune」
```

---

## 作品のURL
```
https://app.eventtune.link
```
> GitHub リポジトリURLは「関連リンク」に併記（本番URLと二重掲載可）。

---

## 概要（上限100字・SNS共有時に表示）

**採用案（90字）**
```
イベント後のバラバラなExcelを投げ込むだけで、AIエージェントが意味で統合。参加者一人ひとりへ根拠つきの個別フォローと、費用対効果の振り返り分析を、熱が冷めないうちに届けます。
```

代替案（71字・さらに短く）
```
イベント後の散らばったExcelを投げ込むだけ。AIエージェントが意味で統合し、根拠つきの個別フォローと費用対効果の振り返りを翌日に届けます。
```

---

## ライセンス
**表示する：Creative Commons Attribution CC BY 4.0**
> ハッカソン公開方針に沿って CC BY を選択。最終確定前にチームで合意を。

---

## システム構成（Markdown で入力 / 1枚目に構成図画像）

> `architecture.png`（`architecture.svg` から書き出し・960×780）を「システム構成」画像欄にアップロードし、本文は以下を貼り付け。

```markdown
EventTune は「カオスなイベントデータをオントロジーに統合し、AIエージェントがその上で働くマルチテナント SaaS」です。設計思想は一言で **「LLM の知能はプロンプトではなく"構造"で統治する」**。

1. **投入** — スペース（テナント）を作り、イベントの名簿・アンケート・ブース記録・費用を、**Excel/CSV に加え Word/PDF/PowerPoint（テキスト抽出）** のままチャット画面にドラッグ投入。
2. **意味統合** — `DataIntegrationAgent`（Gemini on Vertex AI／**google-genai 直呼びの決定論パイプライン**。AI は変換仕様を生成し Python が機械適用する）が列名・形式のゆらぎを吸収し、**OSI セマンティックレイヤー（星座型オントロジー：Person / Account / Product / Content / Event の5マスタ＋ファクト）** へ統合。取り込みは**スペック駆動の統一8ステージ**（Read→Understand→Confirm→Interpret→Conform→Bind→Derive→Report）で走り、AI が作った対応づけプランを**確認画面で承認 → 承認したプランがそのまま実行**。解決できない行はサイレントに捨てず「**保留（pending）**」として理由つきで必ず報告する。各エンティティに自然文要約 `appeal_summary` と埋め込み `appeal_vector`（gemini-embedding-001 / 768次元）を付与。
3. **分析** — `MarketingAgent`（**唯一の Google ADK 自律エージェント** + Gemini）にチャットで質問。費用対効果や振り返りは **Vertex AI Agent Engine のサンドボックス上で AI が生成した Python を実データに対して実行**し、コードと結果の両方を提示（Code Interpreter）。「それっぽい数字」を答えさせない。
4. **個別フォロー生成** — **アプローチ方法（メール／架電／個別資料）と到達可否・到達可能総数の提示から始め、対象人数で方式を分岐**（Human-in-the-Loop）。少人数（目安15名以下）は**個別方式**で1人ずつ実名のフル文面を生成。多数は**セグメント方式**：AI が切り口（軸）を設計 → 人が承認 → 分類 → バケット数 K 回だけパターン生成 → 各人への組み立ては**決定論 Python（LLM 呼び出しゼロ）**で LLM コストを O(N) から O(K) へ。どちらの方式でも各文面ブロックに採用理由 `reason_for_inclusion` が必ず残る（Auditable AI）。
5. **デプロイ / 運用（DevOps）** — Cloud Run（asia-northeast1）/ Firestore / Firebase Authentication・Storage・App Hosting。**Terraform ＋ GitHub Actions（Workload Identity Federation のキーレス認証）** で継続デプロイ。設計判断は 17 本の ADR に「何を採用し何を撤回したか」まで記録。

### 技術スタック
- フロント：Next.js 15（App Router / SSR）+ React 19 + TypeScript + Tailwind CSS
- バック：Python 3.12 + FastAPI + Pydantic v2（オントロジー = 型の単一真実源）+ pandas
- AI：Google ADK（MarketingAgent の自律実行）+ Gemini（Vertex AI）+ gemini-embedding-001 + Agent Engine（Code Interpreter / Session）
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
## この作品で解決したいこと（課題・想定ユーザー・特徴）

> DevOps × AI Agent Hackathon（Findy / Google Cloud）に向けて開発した作品です。

**① 解決したい課題と背景**
イベント（展示会・セミナー・カンファレンス）後のデータは、毎回フォーマットの違う名簿・アンケート・接客メモ・費用がバラバラに散らばり、整形も振り返りもフォローも担当者の手作業と記憶に依存します。「データドリブン」が最も求められる領域なのに、現場のデータは全社で最もカオス。いま AI 活用の成否は基盤モデルの性能ではなく **「AI-Ready なデータと文脈の整備」** で決まります ── この最大のボトルネックを、イベントマーケティングで解きます。

**② 想定する利用ユーザー**
BtoB 企業でイベント・展示会・セミナーを運営する **マーケティング担当者**、および施策の費用対効果と再現性・継承を求める **その上司・マーケティング組織**。数名規模の個別フォローから数百名規模のセグメント配信まで対応します。

**③ プロダクトの特徴**
バラバラなファイルを投げ込むだけで、AI エージェントが意味統合（オントロジー化）。**(1) 実データ上で Python を実行する"嘘をつかない"分析**、**(2) 一人ひとりへ根拠つきの個別フォロー（100人100通り・人間が最終承認）**、**(3) 型による統治（テナント分離・全出力に根拠を強制する Auditable AI）** を、Cloud Run / Vertex AI / Gemini / Google ADK で構成。プロトタイプではなく、実運用を見据えたフルサイクルで開発しました。

---

> 以下、本作品への想いを SpeakerDeck の3つのスライドと同じ3章に整理しました。各章の冒頭にスライドを埋め込んでいます（① プロダクト価値／② 技術的な課題認識と着想・技術選定・アーキテクチャ／③ 開発スタイル・DevOps）。

---

## 第1章｜プロダクト価値 — イベントの"やりっぱなし"を、翌日の成果に変える

SPEAKERDECK_URL_1
[▶ スライド①「プロダクト価値」を見る（SpeakerDeck）](https://speakerdeck.com/vdnih/ibentomakeaieziento-eventtune-purodakutojia-zhi-ibentono-yaritupanasi-wo-yi-ri-nocheng-guo-nibian-eru)

### イベント後、こんな「やりっぱなし」になっていませんか？

展示会・セミナー・カンファレンス。終わった瞬間から、現場と組織の両方で"溶けていく時間"が始まります。

**現場のマーケ担当のリアル**
- フォルダはカオス。必要なファイルを探すだけで時間が溶ける。
- 「この前のイベント、どうだった？」に即答できず、振り返りレポートに1週間かかる。
- 費用は請求書を1枚ずつ Excel へ手入力。全額を出すのがひと苦労。
- フォローは結局「ご来場ありがとうございました」の一括送信。
- 振り返りの形式も置き場もバラバラで、過去と比較できない。

**上司・組織のリアル**
- 施策がやりっぱなしで、改善につながらない。
- 担当が辞めたら、そのイベントは誰にも引き継げない。
- 過去数年の投資対効果を横断で見たいのに、すぐ出てこない。
- 成果が上がる施策を見極めて、組織成果を拡大したい。
- ノウハウが個人に閉じ、会社の資産になっていない。

### あなたは企画と改善に集中する。あとは EventTune が。

事実ベースの振り返りはエージェントに任せ、マーケターは「次どうするか」に集中できます。

| これまで | EventTune で |
| --- | --- |
| 振り返りレポートに1週間 | 翌日に、根拠つきで自動生成 |
| 一括送信のフォロー | 一人ひとりの課題に合わせた100人100通り |
| 「あのイベント、どうだった？」に即答できない | 実データで、その場で即答 |
| 過去実績・過去資料が探せない | 過去の投資対効果・使用資料がいつでもすぐわかる |
| 担当依存でブラックボックス | 施策が"会社の資産"として蓄積・継承 |

使い方は「投げ込んで、チャットするだけ」。形式がバラバラな名簿・アンケート・接客メモ・費用（CSV / PDF / Excel / Word / PowerPoint）をそのまま投入し、チャットで頼むだけ。3つの力で成果に変えます。

1. **意味統合（オントロジー化）** — 形式も列名もバラバラなファイルを、「誰が・どのイベントで・何に興味を示し・いくらかかったか」へ AI が自動で接続。事前のデータ整形は不要。
2. **実データで即答** — AI が"それっぽい数字"を勘で言うことはない。集計コードを自ら書いて実データ上で実行し、ROI・前回比・イベント横断比較をコードごと検証できる。
3. **100人100通りを根拠つき** — 一人ひとりの課題に基づく個別フォローを生成。送信前に必ず人が承認（HIL）。「なぜこの人にこの内容か」の判断根拠がすべて残る。

イベント前は過去の類似実績から"今回どれくらいの結果になりそうか"をシミュレーションし、企画テーマも提案。イベント後は意味統合 → 振り返り・ROI → 個別フォローまで一気通貫で伴走します。

### なぜ、汎用AIでも MA でもなく EventTune なのか

| | 汎用AIチャット | MA組み込みAI | EventTune |
| --- | --- | --- | --- |
| データの前提 | 毎回コピペ。会話が終われば忘れる | CRMが綺麗に整っている前提が必須 | バラバラのCSV・メモをそのまま投入 |
| 個別化 | 1通ずつ対話。100人分は破綻する | スコアベースの定型シナリオ | セグメント設計から実名入り生成まで一括 |
| 判断の根拠 | 出力だけ。理由はブラックボックス | スコア算出ロジックが見えにくい | 分類・文面・分析に「理由」「検討経緯」が残る |

安心して任せられる理由は3つ。**勝手に送らない**（承認なしの全件送信はしない／人が必ず最終判断）、**嘘を書かない**（自社にない機能や根拠のない出力は作らない）、**準備ゼロで始まる**（事前のデータ整形もフォーマット統一も不要）。そして会社にとっては、属人的な"暗黙知"を引き継げる資産へ。担当の退職でイベント運用が止まるリスクをなくします。

---

## 第2章｜技術的な課題認識と着想・技術選定・アーキテクチャ — 「LLMの知能は"構造"で統治する」

SPEAKERDECK_URL_2
[▶ スライド②「技術・アーキテクチャ」を見る（SpeakerDeck）]([SPEAKERDECK_URL_2](https://speakerdeck.com/vdnih/eventtune-intelligence-1))

### 課題認識 — AIの勝負は「モデル」ではなく「データと文脈」で決まる

「魔法のように使える AI」と「的外れな AI」を分けるのは、プロンプトの工夫でも基盤モデルの性能差でもありません。**AI が読み込むデータと文脈（コンテキスト）が、どれだけ綺麗に整っているか**の一点です。AIの性能競争は一段落し、真のボトルネックは **「AI-Ready なデータと文脈を整えること」** に移りました。

その課題が最も濃く現れるのがマーケティングです。**「データドリブン」が最も求められる領域なのに、現場のデータは全社で最もカオス** ── イベントごとに列の違う Excel、営業の殴り書きメモ、PowerPoint の戦略資料。「データは CRM にある」という幻想の裏で、実態は非定型なデータの海に溺れている。ここを AI が理解できる形に整えることにこそ、解くべき最大の価値があります。（→ 着想の全文は `MOTIVATION.md`）

### 着想 — 人間が「意味の型（オントロジー）」を設計し、その上で AI が働く

カオスを整える鍵として、**イベントマーケティングのために私たち自身が考え抜いて設計したオントロジー（意味の型）** をアーキテクチャの心臓部に据えました。型は AI に作らせません。

1. **Human Design** — 人間が Person / Account / Product / Content / Event の意味の骨格を強固に設計・固定する。
2. **AI Mapping** — 形式のバラバラなファイルを、AI が列名・形式のゆらぎを読み解いてその型へ正確に流し込む。
3. **Autonomous Execution** — 意味が接地した基盤の上で初めて、AI がハルシネーションを抑え高精度に自律実行する。

設計思想は一言で **「LLM の知能はプロンプトではなく"構造"で統治する」**。意味はセマンティックレイヤーへ、権限は型へ、判断根拠は非 null フィールドへ、コストは決定論へ押し込み、AI には AI にしかできない仕事だけを残します。

### アーキテクチャ — 2つのエージェントを"あえて別方式"で設計し、OSI セマンティックレイヤーで繋ぐ

主役は2つのエージェントですが、**役割の性質が正反対なので実装方式もあえて分けています**。`DataIntegrationAgent` は散在データを **統一8ステージ**（Read→Understand→Confirm→Interpret→Conform→Bind→Derive→Report）で意味統合する担当。取り込みに求められるのは**再現性と完全性**（全行の行き先が必ず記録される）なので、AI に制御フローを握らせる自律エージェントではなく、**google-genai 直呼びの決定論パイプライン**にした。AI は「列 → 意味」の変換仕様を作るだけで、ステージ順序・永続・名寄せ・テナント境界は人間が書いたコードが固定する（エージェント主導取り込みはステージ飛ばしを構造的に防げず不採用＝ADR-013/015）。一方 `MarketingAgent` は「何を分析し、どう個別フォローを組むか」＝**制御フロー自体が AI の仕事**なので、**Google ADK による唯一の自律エージェント**（Runner + Session）にしている。両者は **OSI セマンティックレイヤー（星座型オントロジー：5マスタ＋ファクト）** を共有します。かつての「Event 中心のフラット構造」を撤回し、Person・Account・Product・Content・Event が Attendance / Interest などのファクトで結びつく星座型へ進化させました（ADR-008）。各エンティティには自然文要約と埋め込みベクトル（gemini-embedding-001）を付与し、関心はベクトルのコサイン類似度で意味検索します。

技術を統治するための4本柱：

- **① オントロジー接地** — AI は「CSV の列」ではなく「Person が Event に参加し Product に関心を持つ」という意味の上で推論。イベント横断比較やマルチホップ分析が構造的に可能。
- **② コストを決定論で殺す（O(N)→O(K)）** — 少人数（目安15名以下）は1人ずつ実名でフル生成。多数はセグメント方式に切替え、パターン生成はバケット数 K 回だけ・各人への組み立ては決定論 Python（LLM 呼び出しゼロ）。500人でも LLM を500回叩かない（ADR-016）。
- **③ 型によるテナント分離** — AI ツールはスコープ済みクライアントを closure で受け取り、シグネチャから space_id を排除。AI が他テナントを名指しすることすら構造的に不可能。
- **④ Auditable AI** — `reason_for_inclusion` などの根拠フィールドを Optional にしない規約。数値分析は Vertex AI Agent Engine のサンドボックスで AI 生成 Python を実データに対して実行し、コードと結果を可視化（Code Interpreter / ADR-009）。「それっぽい数字」を答えさせない。

さらに、自社の機能・本質価値（L1/L2）は固定し、顧客の悩み（L3）だけ AI が生成する **Static Core & Dynamic Context**。捏造禁止・1メール1機能のガードレールと承認ゲートで、圧倒的な個別化とブランド一貫性を両立します（ADR-007）。

### 技術選定

- フロント：Next.js 15（App Router / SSR）+ React 19 + TypeScript + Tailwind CSS
- バック：Python 3.12 + FastAPI + Pydantic v2（オントロジー = 型の単一真実源）+ pandas
- AI：Google ADK（MarketingAgent の自律実行）+ Gemini（Vertex AI）+ gemini-embedding-001 + Agent Engine（Code Interpreter / Session）
- 基盤：Cloud Run（asia-northeast1）/ Firestore / Firebase / Cloud Storage / Artifact Registry

このアーキテクチャの選択そのものが、「ChatGPT に CSV を貼ればいいのでは？」への最終回答であり、コモディティ化しない市場防御性（モート）を生み出しています。

---

## 第3章｜開発スタイル・DevOps — つくり方そのものが DevOps だった

SPEAKERDECK_URL_3
[▶ スライド③「開発・DevOps」を見る（SpeakerDeck）](https://speakerdeck.com/vdnih/ibentomakeaieziento-eventtune-kai-fa-sutairudevops-tukurifang-sonomonoga-devops-datuta)

DevOps がテーマのハッカソンだからこそ、成果物だけでなく「まわし方」そのものを見てほしい。EventTune は **AI（Claude）をペアプログラマに据え、テスト・CI/CD・ADR で守られたフルサイクル開発** でつくりました。以下はすべて Git 履歴と GitHub Actions の実績で裏取りした事実です。（→ 全文は `DEVOPS_APPEAL.md`）

### 人間 × AI のペア開発を「PR 単位」で回す

約5.5週間で **50 コミット / 39 の Pull Request（すべてマージ済み）**、うち **51 コミットが `Co-Authored-By: Claude`** ── ほぼ全履歴が人間×AIの共同作業です。人間が「何を・なぜ作るか」を決め（YAML 概念モデルや ADR で承認）、Claude が「どう作るか」を実装 PR に落とす。AI に設計の主導権は渡しません。

### CI をマージ前ゲートにする

Pull Request をトリガーに **4ジョブを並列**（backend-test：ruff check ＋ ruff format --check ＋ pytest／backend-integration：Firestore・Auth エミュレータ上の pytest／frontend-build：lint ＋ typecheck ＋ Vitest ＋ next build／e2e-smoke：Playwright）。1つでも落ちればマージを止めます。**CI は27回走り20成功/7失敗** ── この7失敗こそ「マージ前にゲートが問題を捕捉した」証拠。エミュレータ用 ID には `demo-` プレフィックスを強制し、テストが本番 Firestore に誤接続することを構造的に不可能にしています。

### キーレス CD で「とどける」

`main` への push で本番へ自動デプロイ。**Workload Identity Federation（キーレス認証）** でサービスアカウント鍵をリポジトリに一切持たず、漏洩する鍵がそもそも存在しない。`dorny/paths-filter` で変更パスに応じ Cloud Run / Firebase を選択的にデプロイし、Cloud Run はイメージを `github.sha` でタグ付け。**CD は20回走り15成功/5失敗** で、失敗は IAM を最小権限から実運用で締め上げた反復としてそのまま記録に残ります。IaC は「全部 Terraform」のアンチパターンを避け、Terraform（18ファイル）と Firebase CLI を手段と対象で責務分割しました（ADR-012）。

### テストという安全網 ＝ 恐れずに大改修できる

DevOps の本質は速さではなく、**壊れたらすぐ分かる状態を保ったまま大胆に変えられること**。backend unit 10本 ＋ integration 7本（テナント分離まで検証）＋ frontend 7ファイルの網があったからこそ、取り込みパイプラインを一度で諦めず作り直せました（ADR-011→013→015→016）。OSI セマンティックレイヤーへの全面移行やルートの一括リネームも、テストとエミュレータ CI が守るから踏み切れた成果です。

### 意思決定を ADR に残す（撤回まで含めて）

`docs/ADR.md` に **17本の ADR**。採用理由だけでなく、**やめた判断も記録**します（Event 中心設計の撤回、AI 直接抽出の撤回、機能しなかった「由来を追う」UI の撤去など）。何を捨てたかまで残すことが、意思決定の誠実さと再現性を担保します。加えて開発規律（PR 前に ruff format --check／Python は uv／データモデルは YAML 概念モデルを先に承認／クラウド仕様は MCP で最新 docs を裏取り）を AI の永続メモリに書き込み、全 PR で自動的に守らせています。

Claude が PR を刻み、CI がマージ前に守り、キーレス CD が本番へ届け、ADR が判断を残し、テストが大改修を支える。**プロトタイプではなく、実運用を見据えたフルサイクル**。EventTune は「AI エージェントを載せたプロダクト」であると同時に、「AI エージェントと共に回した DevOps」そのものです。

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
