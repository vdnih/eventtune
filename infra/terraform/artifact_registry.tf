# Cloud Run 用の Docker イメージ置き場。CI がここに push する。
resource "google_artifact_registry_repository" "mmg" {
  project       = var.project_id
  location      = var.region
  repository_id = "eventtune"
  format        = "DOCKER"
  description   = "Container images for eventtune-api (Cloud Run)"

  depends_on = [google_project_service.enabled]
}
