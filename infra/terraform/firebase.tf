# 既存の Firebase 有効化と Web アプリを import して正典化する。
# google_firebase_* は google-beta プロバイダ。
resource "google_firebase_project" "default" {
  provider = google-beta
  project  = var.project_id

  depends_on = [google_project_service.enabled]
}

# フロントの NEXT_PUBLIC_FIREBASE_* の発行元。App Hosting backend の app_id にも使う。
resource "google_firebase_web_app" "default" {
  provider     = google-beta
  project      = var.project_id
  display_name = "EventTune"

  # 削除しても基盤アプリは残す（誤 destroy 時の保険）
  deletion_policy = "ABANDON"

  depends_on = [google_firebase_project.default]
}

# 参考: Web SDK 設定値（apphosting.yaml / フロント env の確認に使える）
data "google_firebase_web_app_config" "default" {
  provider   = google-beta
  project    = var.project_id
  web_app_id = google_firebase_web_app.default.app_id
}
