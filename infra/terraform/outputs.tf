output "cloud_run_url" {
  description = "mmg-api の URL（apphosting.yaml の NEXT_PUBLIC_API_URL / frontend_origin 設定に使う）"
  value       = google_cloud_run_v2_service.mmg_api.uri
}

output "artifact_registry_repo" {
  description = "CI が push する Docker リポジトリのパス"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.mmg.repository_id}"
}

output "github_deployer_sa_email" {
  description = "GitHub Actions が借用するデプロイ SA"
  value       = google_service_account.github_deployer.email
}

output "wif_provider" {
  description = "GitHub Actions の workload_identity_provider に設定する値"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "firebase_web_app_id" {
  description = "App Hosting backend / フロント設定で使う Web アプリ ID"
  value       = google_firebase_web_app.default.app_id
}
