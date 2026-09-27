# SpeakerDeck 掲載メタデータ（コピペ用）— EventTune

> 3スライドを SpeakerDeck にアップロードする際の title / description。
> いずれも「DevOps × AI Agent Hackathon（Findy / Google Cloud）」向けに開発した作品である旨を明記。
> アップロード後の SpeakerDeck URL は `PROTOPEDIA_SUBMISSION.md` のストーリー各章冒頭（`SPEAKERDECK_URL_1/2/3`）に貼り付ける。

---

## ① プロダクト価値

**Title**
```
イベントマーケAIエージェント「EventTune」| プロダクト価値 — 散らばったイベントデータを、集約・分析・活用する
```

**Description**
```
「DevOps × AI Agent Hackathon（Findy / Google Cloud）」向けに開発した、イベントマーケティングAIエージェント「EventTune」の紹介スライドです（全3部・第1部：プロダクト価値）。

展示会・セミナー・カンファレンス後に散らばったファイル（CSV / PDF / Excel / Word / PowerPoint）を投げ込むだけ。AIエージェントが「集約（意味統合・一元管理）→ 分析（実データ上でコードを実行しROI・前回比・横断比較を即答）→ 活用（一人ひとりへの100人100通りの承認つきフォローと、次の企画への一手）」を担い、イベントの「やりっぱなし」をなくして成果と“組織の資産”へ変えます。「投げ込むだけ」「勝手に送らない・嘘を書かない」「すべての判断に“なぜ”が残る」「重ねるほど賢くなる」という4つのメリットを、現場マーケ担当と組織の両視点、汎用AI・MAとの比較から解説します。

▶ 全3部：①プロダクト価値／②技術・アーキテクチャ／③開発スタイル・DevOps
```

---

## ② 技術的な課題認識と着想・技術選定・アーキテクチャ

**Title**
```
イベントマーケAIエージェント「EventTune」| 技術・アーキテクチャ — LLMの知能は"構造"で統治する
```

**Description**
```
「DevOps × AI Agent Hackathon（Findy / Google Cloud）」向けに開発した、イベントマーケティングAIエージェント「EventTune」の技術スライドです（全3部・第2部：技術的な課題認識と着想・技術選定・アーキテクチャ）。

設計思想は『LLMの知能はプロンプトではなく"構造"で統治する』。人間が設計したオントロジー（OSIセマンティックレイヤー／星座型）にデータを意味接地し、2つの自律エージェントが分析と個別フォローを実行します。Google ADK + Gemini（Vertex AI）、Agent Engine（Code Interpreter）、Cloud Run、Firestore を用いたアーキテクチャに加え、コストを O(N)→O(K) に抑える決定論設計、型によるテナント分離、根拠を強制する Auditable AI までを解説します。

▶ 全3部：①プロダクト価値／②技術・アーキテクチャ／③開発スタイル・DevOps
```

---

## ③ 開発スタイル・DevOps

**Title**
```
イベントマーケAIエージェント「EventTune」| 開発スタイル・DevOps — つくり方そのものが DevOps だった
```

**Description**
```
「DevOps × AI Agent Hackathon（Findy / Google Cloud）」向けに開発した、イベントマーケティングAIエージェント「EventTune」の開発プロセススライドです（全3部・第3部：開発スタイル・DevOps）。

AI（Claude）をペアプログラマに据え、テスト・CI/CD・ADR に守られたフルサイクル開発で構築しました。GitHub Actions によるマージ前ゲート（4ジョブ並列）、Workload Identity Federation のキーレス CD、Terraform による IaC、17本の ADR（撤回した判断まで記録）など、『AIの不確実性を DevOps の力で統治する』実運用志向の開発手法を、Git 履歴と GitHub Actions の実績（39 PR 全マージ 等）で裏取りして紹介します。

▶ 全3部：①プロダクト価値／②技術・アーキテクチャ／③開発スタイル・DevOps
```
