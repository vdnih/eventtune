# Cloud Run 用の Docker イメージ置き場。CI がここに push する。
resource "google_artifact_registry_repository" "mmg" {
  project       = var.project_id
  location      = var.region
  repository_id = "eventtune"
  format        = "DOCKER"
  description   = "Container images for eventtune-api (Cloud Run)"

  # コスト縮退方針（ADR-018）: push のたびにイメージが増え続けストレージ課金が積み上がるため、
  # 直近3件だけ残して古いバージョンを削除する。まず dry_run で削除予定を確認してから
  # false にして本適用する（誤削除防止）。
  cleanup_policy_dry_run = true

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = 3
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"

    condition {
      older_than = "2592000s" # 30日
    }
  }

  depends_on = [google_project_service.enabled]
}
