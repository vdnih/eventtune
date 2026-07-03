# EventTune 改称 — 手作業手順書

`rename/event-tune` ブランチのコード変更（マージ済み想定）を前提に、実際の GCP/Firebase/
GitHub/DNS 側の切り替えをユーザー自身が行うための手順。背景・意思決定の詳細は
[`docs/ADR.md`](ADR.md) の ADR-014 を参照。

## 前提

- GCP/Firebase プロジェクト ID は `marketing-mail-generator` を**維持**する
  （新規プロジェクト作成はクォータ制限のため不可）。Terraform 管理下の個別リソース
  （Cloud Run・Artifact Registry・サービスアカウント・App Hosting backend）のみ
  `eventtune-*` に作り直す。
- ドメイン `eventtune.link`（AWS Route 53 で取得済み）のうち、このアプリには
  `app.eventtune.link` を割り当てる。トップレベル `eventtune.link` は将来の
  ランディングページ用に予約し、今回は触らない。
- 本番データはまだ無いため、Terraform 管理下リソースの destroy → create は許容する。

## ユーザーから見える箇所に旧名が残る2箇所

プロジェクト ID を維持する以上、何もしなければ以下がユーザーの目に触れる。

| # | 露出箇所 | 見え方 | 深刻度 |
|---|---|---|---|
| 1 | App Hosting の既定配信 URL | `https://eventtune-frontend--marketing-mail-generator.<region>.hosted.app` | 高（常時アドレスバーに表示） |
| 2 | Firebase Auth の authDomain | `marketing-mail-generator.firebaseapp.com` | 低（ログインポップアップの一瞬のみ） |

露出箇所1はカスタムドメイン（手順5）で解消する。露出箇所2は影響が小さいため今回は対処しない。

---

## 進捗状況（2026-07-03 更新）

| # | 手順 | 状態 |
|---|---|---|
| 1 | GitHub リポジトリ名の変更 | ✅ 完了（`vdnih/eventtune`、リモートURL更新済み） |
| 2 | Developer Connect 連携の確認 | ✅ 完了（自動追従を検出・tfvars側のズレを修正・apply済み） |
| 3 | Terraform apply（リソース作り直し） | ✅ 完了（`Apply complete! Resources: 16 added, 2 changed, 16 destroyed`） |
| 3.5 | `frontend_origin`（CORS）の暫定修正 | ✅ 完了・apply済み（`terraform plan` が No changes になることを確認済み） |
| 4 | Cloud Run URL の反映 | ✅ 完了（`frontend/apphosting.yaml` 更新済み） |
| 5〜7 | カスタムドメイン設定・Auth Authorized domains・CORS本反映 | 🟡 進行中（DNSレコード2件待ち、下記参照） |
| 8 | `github_repo` 変数の反映 | ✅ 手順3の apply に含まれ反映済み |
| 9 | Firebase Auth ブランディングの反映 | ⬜ 未着手 |
| 10 | 商標クリアランス | ⬜ 未着手（対外公開前まででよい） |

### 実際に確定した値（手順3 apply の出力）

```
artifact_registry_repo   = asia-northeast1-docker.pkg.dev/marketing-mail-generator/eventtune
cloud_run_url            = https://eventtune-api-bd2jolesza-an.a.run.app
firebase_web_app_id      = 1:974233950009:web:75bbffb7465a269bf8bae8（変更なし）
github_deployer_sa_email = github-deployer@marketing-mail-generator.iam.gserviceaccount.com（変更なし）
wif_provider             = projects/974233950009/locations/global/workloadIdentityPools/github-pool/providers/github-oidc（変更なし）
```

> **注記**: Cloud Run v2 の URL はプロジェクト番号ベースの単純な形式
> （`https://<service>-<project番号>.<region>.run.app`）ではなく、ハッシュを含む形式
> （`https://<service>-<ハッシュ>-<region略称>.a.run.app`）だった。事前にコードへ埋めていた
> 予測値は誤りだったため、`terraform output cloud_run_url` の実値で上書き済み。
> 同様に App Hosting の配信 URL も推測せず `terraform output app_hosting_url`
> （今回追加）で確認すること。

### 手順3.5で見つかった問題: CORS が壊れていた

`terraform apply` で App Hosting backend が `mmg-frontend` → `eventtune-frontend` に
作り直されたが、バックエンドの CORS 許可オリジン（`FRONTEND_ORIGIN` env）は
`terraform.tfvars` の `frontend_origin` の値のままだったため、**新しい配信 URL
（`eventtune-frontend--...`）からの API 呼び出しが CORS エラーになる状態**になっていた。
`terraform.tfvars` の `frontend_origin` を暫定的に
`https://eventtune-frontend--marketing-mail-generator.asia-east1.hosted.app`
に更新済み（`terraform plan` で `FRONTEND_ORIGIN` env の1件更新のみになることを確認済み）。
**この変更を反映するため、以下を実行して再 apply が必要:**

```bash
cd infra/terraform
terraform apply
```

（カスタムドメイン設定完了後は、手順7で `https://app.eventtune.link` に再度更新し、
もう一度 apply することになる）

---

## 手順

### 1. GitHub リポジトリ名の変更 ✅ 完了

```bash
gh repo rename eventtune
git remote set-url origin https://github.com/vdnih/eventtune.git
```

### 2. Firebase App Hosting の Developer Connect 連携を確認 ✅ 完了（ズレを検出・修正済み）

Firebase App Hosting の REST API（`firebaseapphosting.googleapis.com`）を直接叩いて
実際の operations を確認したところ、GitHub リポジトリ改称を受けて Developer Connect 側が
リンク名を自動的に `vdnih-marketing-mail-generator` → `vdnih-eventtune` に更新済み
（2026-07-03T05:26:26、backend への `update` オペレーションとして記録されている）。

**ただし `terraform.tfvars` の `app_hosting_repository` は旧リンク名のままだったため、
このまま次の `terraform apply` を実行すると Developer Connect の自動修正を巻き戻し、
連携が壊れる状態だった。** `terraform.tfvars` を新リンク名（`vdnih-eventtune`）に更新し、
`terraform plan` で差分が解消されたことを確認済み。**この変更を反映するため、
手順3.5の apply と合わせて以下を実行すること:**

```bash
cd infra/terraform
terraform apply
```

（適用後の残差分は `codebase.root_directory` の表記ゆれ（`"frontend/"` ⇄ `"/frontend"`）のみで、
今回の改称とは無関係の軽微な既存差分。実害なし。）

### 3. Terraform apply（Cloud Run / Artifact Registry / SA / App Hosting backend のリネーム） ✅ 完了

```bash
cd infra/terraform
terraform plan   # mmg-* → eventtune-* への destroy+create が計画されることを確認
terraform apply
```

- Cloud Run サービス名変更で URL が変わった（実際の値は上記「実際に確定した値」参照）。
- Artifact Registry の中身（過去イメージ）は引き継がれない。次回 CI push で新規作成される。
  不要になった旧 `mmg` リポジトリは以下で手動削除可:
  ```bash
  gcloud artifacts repositories delete mmg --location=asia-northeast1
  ```
- App Hosting backend 名の変更で既定配信 URL も変わった（`terraform output app_hosting_url`）。
- **副作用として CORS が壊れた（上記「手順3.5」参照、修正済み・要再apply）。**

### 4. Cloud Run URL の反映 ✅ 完了

```bash
terraform output cloud_run_url
```

`frontend/apphosting.yaml` の `NEXT_PUBLIC_API_URL` を実際の値に更新済み
（`https://eventtune-api-bd2jolesza-an.a.run.app`）。

### 5. `app.eventtune.link` を App Hosting のカスタムドメインとして追加 🟡 進行中（DNSレコード待ち）

Firebase コンソール → App Hosting → 対象 backend → カスタムドメインの追加 で
`app.eventtune.link` を登録し、表示された検証用 TXT レコード・A/CNAME レコードを
AWS Route 53 の `eventtune.link` ホストゾーンに追加する。DNS 伝播・SSL 証明書発行
（数分〜数十分）を待って `https://app.eventtune.link` で疎通確認する。

> **トップレベル `eventtune.link`（apex）には何も設定しない**（将来のランディングページ用に
> 予約のため）。

#### 現在の状態（2026-07-03 API 直接確認済み）

ドメイン作成操作は既に開始済み（`operation-1783055771547-655ae05e0b59f-...`）だが、
以下2件の DNS レコードが Route 53 に未追加のため `OWNERSHIP_MISSING` / `CERT_VALIDATING`
で止まっている。**Firebase コンソールで再度「ロールアウトの作成中にエラーが発生しました」
と出るのは、この既存の保留中オペレーションと衝突しているため**であり、コード側の問題ではない。
以下のレコードを追加すれば Firebase 側の定期チェックで自動的に解消される（コンソールでの
再試行は不要）。

| 種別 | ホスト名 | 値 |
|---|---|---|
| A | `app.eventtune.link` | `35.219.200.58`（設定済み・確認済み） |
| TXT | `app.eventtune.link` | `fah-claim=004-02-39fa2bf8-8c4b-4d96-b55d-2efac2a84cee` |
| CNAME | `_acme-challenge_2rq7mkdon47fmtoa.app.eventtune.link` | `acbd5da6-b7c5-47a5-bdce-ed0d987046d9.16.authorize.certificatemanager.goog.` |

> レコード値はドメイン追加操作ごとに固有（ランダムなチャレンジ文字列を含む）。もし
> Firebase コンソールで一度ドメイン登録をキャンセルして再登録した場合は、上記の値は
> 無効になり新しい値に差し替わる。その場合は下記コマンドで最新の要求レコードを再取得できる:
> ```bash
> TOKEN=$(gcloud auth print-access-token)
> curl -s -H "Authorization: Bearer $TOKEN" \
>   "https://firebaseapphosting.googleapis.com/v1/projects/marketing-mail-generator/locations/asia-east1/operations?pageSize=20" \
>   | grep -A40 'domains/app.eventtune.link'
> ```

### 6. Firebase Auth の Authorized domains に追加

Firebase コンソール → Authentication → Settings → Authorized domains に
`app.eventtune.link` を追加する。**これを忘れると当該ドメインからの Google ログインが
`auth/unauthorized-domain` エラーで失敗する。** `authDomain` の値自体
（`NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`）は `marketing-mail-generator.firebaseapp.com` の
ままで変更不要。

### 7. `frontend_origin`（CORS）を確定ドメインに更新

`terraform.tfvars` の `frontend_origin` を（暫定値の App Hosting 既定 URL から）
`"https://app.eventtune.link"` に更新し、`terraform apply` を再実行してバックエンドの
CORS 許可オリジンを最終ドメインに合わせる（バックエンドは単一オリジンのみ許可する実装のため、
実際にユーザーがアクセスするドメインと一致している必要がある）。

### 8. `github_repo` 変数の反映 ✅ 完了

手順1完了後、`infra/terraform/variables.tf` の `github_repo`（コード側で既に `eventtune` に
更新済み）が実態と一致する。手順3の apply に含めて WIF の `attribute_condition` を最新化した。

### 9. Firebase Auth ブランディングの反映

```bash
firebase deploy --only auth --project marketing-mail-generator
```

あわせて Google Cloud Console →「APIs & Services」→「OAuth consent screen」のアプリ名も
`EventTune` に手動更新する。

### 10. 商標クリアランスに関する留保

「EventTune」は `NAMING_PROPOSAL.md` §6 の簡易調査対象外（調査済みなのは旧名 EventWeave のみ）。
ドメイン（`eventtune.link`）は既に確保済みだが、商標としての衝突確認は別途必要。対外公開前に
EventTune 単独での商標調査を行うことを推奨する。正式な商標調査（J-PlatPat 第35類・第42類、
USPTO）が完了するまでは、プレスリリース・広告出稿など対外的な商標的使用は保留する。

---

## 完了確認

- `https://app.eventtune.link` でアプリにアクセスでき、Google ログインが
  `auth/unauthorized-domain` エラーなく成功する
- アドレスバーに `hosted.app` や `marketing-mail-generator` が表示されない
- `terraform plan` が no-op になる（ドリフト無し）
- GitHub Actions の CD（`deploy.yml`）が新しい `SERVICE=eventtune-api` / `REPO=eventtune` で
  正常にデプロイできる
