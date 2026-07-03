# Cloud Run 実行 SA（eventtune-api-sa）と IAM ロール。docs/INFRA_ARCHITECTURE.md の表に対応。
resource "google_service_account" "mmg_api" {
  project      = var.project_id
  account_id   = "eventtune-api-sa"
  display_name = "eventtune-api Cloud Run runtime SA"
}

locals {
  eventtune_api_roles = [
    "roles/datastore.user",                  # Firestore 読み書き
    "roles/storage.objectViewer",            # GCS アップロード読み取り
    "roles/aiplatform.user",                 # Vertex AI(Gemini) / Agent Engine
    "roles/firebase.sdkAdminServiceAgent",   # Firebase Auth ID Token 検証
    "roles/secretmanager.secretAccessor",    # 将来の秘密用（現状 env は平文・ADR-012）
    "roles/developerconnect.user",           # Developer Connect 利用権限 (App Hostingビルド用)
    "roles/developerconnect.readTokenAccessor", # デプロイトークンのフェッチ権限 (App Hostingビルド用)
    "roles/artifactregistry.writer",         # Artifact Registry の読み書き (App Hostingビルド用)
    "roles/logging.logWriter",               # ビルドログの書き込み (App Hostingビルド用)
  ]
}

resource "google_project_iam_member" "mmg_api" {
  for_each = toset(local.eventtune_api_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.mmg_api.email}"
}
