# GitHub Actions 用のキーレス認証（Workload Identity Federation）とデプロイ SA。
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # 当該リポジトリのワークフローだけに限定。
  attribute_condition = "assertion.repository == '${var.github_owner}/${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_deployer" {
  project      = var.project_id
  account_id   = "github-deployer"
  display_name = "GitHub Actions deployer"
}

locals {
  github_deployer_roles = [
    "roles/run.admin",                 # Cloud Run へ deploy
    "roles/artifactregistry.writer",   # イメージ push
    "roles/cloudbuild.builds.editor",  # gcloud builds submit
    "roles/firebaserules.admin",       # firestore/storage ルール配信
    "roles/datastore.indexAdmin",      # firestore インデックス配信
    "roles/serviceusage.serviceUsageViewer", # API 有効化状況の取得 (Firebaseデプロイ用)
    "roles/firebasestorage.admin",     # Firebase Storage 管理権限 (デフォルトバケット取得用)
    "roles/firebase.admin",            # Firebase 管理者権限 (プロジェクトメタデータ参照用)
  ]
}

resource "google_project_iam_member" "github_deployer" {
  for_each = toset(local.github_deployer_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

# Cloud Run deploy 時に実行 SA（eventtune-api-sa）として振る舞う権限。
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.mmg_api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

# WIF プリンシパル（当該リポジトリ）が github-deployer SA を借用できるようにする。
resource "google_service_account_iam_member" "github_wif_impersonate" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}
