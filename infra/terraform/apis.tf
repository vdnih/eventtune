# 必要な GCP API を有効化。既に有効でも google_project_service の apply は冪等。
locals {
  services = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "firebaseapphosting.googleapis.com",
    "developerconnect.googleapis.com",
    "identitytoolkit.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.services)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}
