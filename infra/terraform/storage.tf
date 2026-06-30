# Firebase 既定バケット（*.firebasestorage.app）を import し、uploads/ の 7日ライフサイクルを付与。
# Storage の「ルール」は firebase CLI（firebase deploy --only storage）で配信する。
resource "google_storage_bucket" "default" {
  project  = var.project_id
  name     = "${var.project_id}.firebasestorage.app"
  location = var.storage_location

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["uploads/"]
    }
    action {
      type = "Delete"
    }
  }

  # Firebase が管理する属性は import 後に差分化しやすいので無視する。
  lifecycle {
    ignore_changes = [cors, labels]
  }
}

resource "google_firebase_storage_bucket" "default" {
  provider  = google-beta
  project   = var.project_id
  bucket_id = google_storage_bucket.default.id
}
