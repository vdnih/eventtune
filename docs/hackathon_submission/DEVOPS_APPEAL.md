# つくり方そのものが DevOps だった — EventTune 開発スタイル

> DevOps × AI Agent Hackathon（Findy / Google Cloud）向け、開発プロセスのアピール文書。
> プロダクトの価値は `MOTIVATION.md`、技術構成は `PROTOPEDIA_SUBMISSION.md` を参照。本書は「**どう作ったか**」に絞る。

---

## 要旨

EventTune は、**AI（Claude）をペアプログラマに据え、テスト・CI/CD・ADR で守られたフルサイクル開発**でつくりました。DevOps がテーマのハッカソンだからこそ、成果物だけでなく「まわし方」そのものを見てほしい。

約5.5週間（2026-06-03〜07-11）で **50 コミット / 39 の Pull Request（すべてマージ済み）**。その **51 コミットが `Co-Authored-By: Claude`** ── ほぼ全履歴が人間×AIの共同作業です。人間が設計と意思決定を握り、AI が実装 PR を刻む。CI がマージ前に品質を守り、キーレス CD が本番へ届け、判断は 17 本の ADR に残す。以下、すべて Git 履歴と GitHub Actions の実績で裏取りした事実です。

---

## 1. 人間 × AI のペア開発を「PR 単位」で回す

- **50 コミット中 51 が Co-Authored-By: Claude**（マージコミット等を含む全 body 基準）。単発の「AI に一括生成させた」ではなく、**機能・修正・リファクタを PR 単位で細かく積み上げた**履歴が残っています。
- コミットは Conventional Commits（`feat` / `fix` / `refactor` / `chore` / `docs` / `test`）で統一。`#1`〜`#39` の PR 番号が全コミットに紐づき、**変更の単位＝レビュー可能な単位**になっています。
- 役割分担は明確です。**人間が「何を・なぜ作るか」を決め（YAML 概念モデルや ADR で承認）、Claude が「どう作るか」を実装 PR に落とす**。AI に設計の主導権は渡さない ── これはメモリに規律として明文化しています（後述 §7）。

## 2. CI をマージ前ゲートにする（`.github/workflows/ci.yml`）

Pull Request をトリガーに、**4 ジョブを並列**で走らせ、1 つでも落ちればマージを止めます。

| ジョブ | 内容 |
| --- | --- |
| backend-test | `ruff check` ＋ `ruff format --check` ＋ `pytest`（uv / frozen lock） |
| backend-integration | **Firestore / Auth エミュレータ**上で `pytest -m integration` |
| frontend-build | `lint` ＋ `typecheck` ＋ Vitest ＋ `next build` |
| e2e-smoke | **Playwright をエミュレータ上で実行**、失敗時は report を artifact に保存 |

- **実績: CI は 27 回走り、20 成功 / 7 失敗。** この 7 失敗こそ「**マージ前にゲートが問題を捕捉した**」証拠です。緑になってから入る運用が機能していました（CI は PR #18 で導入し、以降の全 PR をカバー）。
- エミュレータ用プロジェクト ID には `demo-` プレフィックスを強制し（`ci.yml` L50 のコメント）、**テストが本番 Firestore に誤接続することを構造的に不可能に**しています。

## 3. キーレス CD で「とどける」（`.github/workflows/deploy.yml`）

`main` への push をトリガーに、本番へ自動デプロイします。

- **Workload Identity Federation（キーレス認証、`id-token: write`）** ── サービスアカウント鍵をリポジトリに一切持ちません。漏洩する鍵がそもそも存在しない。
- `dorny/paths-filter` で**変更パスに応じて Cloud Run / Firebase を選択的にデプロイ**。バックエンド変更時だけ docker build、Firestore ルール変更時だけ rules deploy と、無駄なビルドを避けます。
- Cloud Run はイメージを `github.sha` でタグ付けして Artifact Registry に push → `gcloud run deploy`。**どのコミットが本番で動いているかが常に一意に追える**。
- **実績: CD は 20 回走り、15 成功 / 5 失敗。** 失敗はきれいごとではなく、履歴の権限修正コミット群（`ee4c9f3` CD パイプライン権限、`f225ca0` / `a97dd58` App Hosting のビルド SA 権限付与）と対応します。**IAM を最小権限から実運用で締め上げていった反復**が、そのまま記録に残っています。

## 4. テストという安全網 ＝ 「恐れずに大改修できる」

DevOps の本質は速さではなく、**壊れたらすぐ分かる状態を保ったまま大胆に変えられること**です。

- テスト構成: **backend unit 10 本 ＋ integration 7 本**（`test_auth_boundary` でテナント分離まで検証）、**frontend 7 ファイル**（Vitest unit ＋ Playwright `smoke.spec.ts`）。
- この網があったからこそ、**取り込みパイプラインを一度きりで諦めず作り直せました**：
  - `ADR-011`（依存順の多段へ再設計 / #16）→ `ADR-013`（AI 直接抽出・業務判定を排除 / #19）→ `ADR-015`（統一8ステージへ再建 / #27）→ `ADR-016`（個別対応を3ゲート方式へ / #36）
  - さらに **OSI セマンティックレイヤーへの全面移行**（#11）、ルートの一括リネーム（`/dashboard→/agent` #32）。
- 普通なら「動いているものは触りたくない」規模の改修を、**テストとエミュレータ CI が守るから踏み切れた**。コアを何度も作り直せたのは安全網の直接の成果です。

## 5. 意思決定を ADR に残す（撤回まで含めて）

- `docs/ADR.md` に **17 本の ADR**。採用理由だけでなく、**やめた判断も記録**します。
  - `ADR-013`: AI に直接抽出させる設計を撤回（業務判定は明文化 Python へ）
  - `ADR-017`: UI として機能しなかった「由来を追う」機能の撤去（#38）
  - Event 中心のデータモデルを撤回し星座型 OSI へ
- 「何を採用したか」だけの記録は後から美化できます。**何を捨てたかまで残す**ことが、意思決定の誠実さと再現性を担保します。

## 6. プロセスを規律化した開発ルール（AI への恒久指示）

一度きりの判断で終わらせず、**開発規律を AI の永続メモリに書き込み、全 PR で自動的に守らせています**。

- **PR 前に `ruff format --check` まで必ず通す** ── CI の backend-test ジョブと完全一致。ローカルとゲートで基準がぶれない。
- **Python は `uv`（pip 不使用）** ── CI も `uv sync --frozen` で lock を固定。
- **データモデルは YAML 概念モデルを先に固め、承認後に実装**（doc-first のレビューゲート）。
- **設計判断は必ずドキュメント化**し、マーケ思想とシステム設計は分離。思想 → アーキ → プロンプトをリンクで繋ぐ。
- **クラウド仕様は記憶に頼らず MCP で最新 docs を裏取り** ── 実際にこの規律が Vertex AI Extensions の廃止を発見し、設計を Agent Engine へ切り替えさせました（`ADR-009`）。

## 7. まとめ

Claude が PR を刻み、CI がマージ前に守り、キーレス CD が本番へ届け、ADR が判断を残し、テストが大改修を支える。**プロトタイプではなく、実運用を見据えたフルサイクル**で作りました。EventTune は「AI エージェントを載せたプロダクト」であると同時に、「**AI エージェントと共に回した DevOps**」そのものです。

---

### 数字の出どころ（再現コマンド）

```bash
git rev-list --count HEAD                                   # 50 コミット
git log --format='%b' | grep -c 'Co-Authored-By: Claude'    # 51
gh pr list --state all --limit 100 --json state             # 39 / 39 merged
gh run list --workflow=ci.yml --json conclusion             # 27 run: 20 success / 7 failure
gh run list --workflow=deploy.yml --json conclusion         # 20 run: 15 success / 5 failure
grep -cE '^## ADR-' docs/ADR.md                             # 17
```
