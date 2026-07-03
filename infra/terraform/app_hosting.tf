# Firebase App Hosting（Next.js SSR）。git push で自動ビルド&デプロイ。
#
# 前提（一度きりの手作業・Google 推奨の不可避ステップ）:
#   Firebase コンソール → App Hosting で GitHub を OAuth 連携（Developer Connect 接続）し、
#   対象リポジトリの「git repository link」リソース名を取得して app_hosting_repository に設定する。
#   接続が未設定（空文字）の間は count=0 で apply をブロックしない。

variable "app_hosting_location" {
  description = "App Hosting backend のリージョン（App Hosting 対応リージョンに限る。例: asia-east1）"
  type        = string
  default     = "asia-east1"
}

variable "app_hosting_serving_locality" {
  description = "App Hosting の配信ローカリティ（REGIONAL_STRICT / GLOBAL）"
  type        = string
  default     = "REGIONAL_STRICT"
}

variable "app_hosting_repository" {
  description = "Developer Connect の git repository link リソース名。空なら App Hosting backend は作らない"
  type        = string
  default     = ""
}

resource "google_firebase_app_hosting_backend" "frontend" {
  count = var.app_hosting_repository == "" ? 0 : 1

  provider         = google-beta
  project          = var.project_id
  location         = var.app_hosting_location
  backend_id       = "eventtune-frontend"
  app_id           = google_firebase_web_app.default.app_id
  serving_locality = var.app_hosting_serving_locality
  service_account  = google_service_account.mmg_api.email

  codebase {
    repository     = var.app_hosting_repository
    root_directory = "/frontend"
  }

  depends_on = [google_project_service.enabled]
}
