# Cloud Run 実行 SA（mmg-api-sa）と IAM ロール。docs/INFRA_ARCHITECTURE.md の表に対応。
resource "google_service_account" "mmg_api" {
  project      = var.project_id
  account_id   = "mmg-api-sa"
  display_name = "mmg-api Cloud Run runtime SA"
}

locals {
  mmg_api_roles = [
    "roles/datastore.user",                  # Firestore 読み書き
    "roles/storage.objectViewer",            # GCS アップロード読み取り
    "roles/aiplatform.user",                 # Vertex AI(Gemini) / Agent Engine
    "roles/firebase.sdkAdminServiceAgent",   # Firebase Auth ID Token 検証
    "roles/secretmanager.secretAccessor",    # 将来の秘密用（現状 env は平文・ADR-012）
  ]
}

resource "google_project_iam_member" "mmg_api" {
  for_each = toset(local.mmg_api_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.mmg_api.email}"
}
