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
| 5 | カスタムドメイン設定 | ✅ 完了（DNS 3件すべて反映、`hostState/ownershipState/certState` すべて `ACTIVE`、HTTPS疎通確認済み） |
| 6 | Auth Authorized domains | ✅ 完了（`app.eventtune.link` が登録済みであることをAPIで確認済み） |
| 7 | `frontend_origin`（CORS）本反映 | 🟡 コード修正済み・**要apply**（下記参照） |
| 8 | `github_repo` 変数の反映 | ✅ 手順3の apply に含まれ反映済み |
| 9 | Firebase Auth ブランディングの反映 | ⬜ 未着手 |
| 10 | 商標クリアランス | ⬜ 未着手（対外公開前まででよい） |

### 手順7で見つかった問題: カスタムドメインからのAPI呼び出しがCORSで拒否される

ステップ5(カスタムドメイン)完了後も`terraform.tfvars`の`frontend_origin`は暫定URL
（`eventtune-frontend--marketing-mail-generator.asia-east1.hosted.app`）のままだったため、
**`https://app.eventtune.link`からのAPI呼び出しがCORSエラーになる状態**だった
（`OPTIONS /health`を`Origin: https://app.eventtune.link`付きで叩き、
`Access-Control-Allow-Origin`ヘッダーが返らないことを確認して検出）。
`terraform.tfvars`の`frontend_origin`を`https://app.eventtune.link`に更新済み。
**この変更を反映するため、以下を実行して再applyが必要:**

```bash
cd infra/terraform
terraform apply
```

（`terraform plan`で`FRONTEND_ORIGIN`env変数の更新1件 + 既存の軽微な`root_directory`表記ゆれ
1件のみになることを確認済み）

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

### 5. `app.eventtune.link` を App Hosting のカスタムドメインとして追加 ✅ 完了

Route 53 に A / TXT / CNAME の3レコードを追加し、DNS伝播・所有権検証・SSL証明書発行が完了。
2026-07-03 時点で API 上も `hostState: HOST_ACTIVE` / `ownershipState: OWNERSHIP_ACTIVE` /
`certState: CERT_ACTIVE` を確認し、`https://app.eventtune.link` への実際の HTTPS 疎通
（`HTTP/2 200`、有効な証明書）も確認済み。

> **トップレベル `eventtune.link`（apex）には何も設定していない**（将来のランディングページ用に
> 予約のため、今回は触らず）。

### 6. Firebase Auth の Authorized domains に追加 ✅ 完了

`app.eventtune.link` が Authorized domains に登録済みであることを Identity Toolkit API で
確認済み（`authorizedDomains` に含まれる）。`authDomain` の値自体
（`NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`）は `marketing-mail-generator.firebaseapp.com` の
ままで変更していない（ログインポップアップが一瞬その URL を経由するのみで実害が小さいため）。

### 7. `frontend_origin`（CORS）を確定ドメインに更新 🟡 コード修正済み・要apply

`terraform.tfvars` の `frontend_origin` を（暫定値の App Hosting 既定 URL から）
`"https://app.eventtune.link"` に更新済み。実際に CORS が拒否されていることを
`OPTIONS` リクエストで確認した上での修正（詳細は上記「進捗状況」内の該当セクション参照）。
**以下を実行して反映すること:**

```bash
cd infra/terraform
terraform apply
```

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
