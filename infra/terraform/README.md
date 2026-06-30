# infra/terraform — GCP IaC

`marketing-mail-generator`（単一プロジェクト）の GCP インフラを Terraform で正典化する。
責務分割の全体像は `docs/INFRA_ARCHITECTURE.md` / ADR-012 を参照。

## 何を Terraform が持つか
- API 有効化 / Artifact Registry / Cloud Run(`mmg-api`) / 実行 SA + IAM
- Firestore データベース本体 / 既定 Storage バケット + ライフサイクル
- Firebase プロジェクト紐付け / Web アプリ / App Hosting backend
- GitHub Actions 用 WIF + デプロイ SA

## 何を Terraform が持たないか（意図的）
- Firestore **ルール/インデックス**・Storage **ルール** → `firebase deploy`
- フロントのビルド&デプロイ → App Hosting（git push 自動）
- Auth(Google Sign-In) → Firebase 管理のまま（auth.tf 参照）
- Agent Engine → `backend/scripts/provision_agent_engine.py`（ID を変数注入）

## 初期セットアップ手順

```bash
# 0) state バケット（1回だけ手動）
gsutil mb -l asia-northeast1 -p marketing-mail-generator gs://marketing-mail-generator-tfstate
gsutil versioning set on gs://marketing-mail-generator-tfstate

# 1) Agent Engine を作成して ID を控える（未作成なら）
cd ../../backend && uv run python scripts/provision_agent_engine.py

# 2) 変数を用意
cd ../infra/terraform
cp terraform.tfvars.example terraform.tfvars   # agent_engine_*, frontend_origin を埋める

# 3) Web アプリ ID を imports.tf の REPLACE_WITH_WEB_APP_ID に設定
firebase apps:list WEB --project marketing-mail-generator

# 4) 初期化・取り込み・適用
terraform init
terraform plan      # import ブロックにより既存リソースが取り込まれる
terraform apply
```

## App Hosting を有効化する（任意・フロント自動デプロイ）
1. Firebase コンソール → App Hosting で GitHub を OAuth 連携（Developer Connect）。
2. git repository link のリソース名を取得し `terraform.tfvars` の `app_hosting_repository` に設定。
3. `app_hosting_location` が App Hosting 対応リージョンであることを確認（既定 asia-east1）。
4. `terraform apply`。

## ドリフト検知
日常的に `terraform plan` を実行し no-op を確認（手作業の混入検知）。
