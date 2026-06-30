# バックエンド API（FastAPI + ADK）。仕様は docs/INFRA_ARCHITECTURE.md を踏襲。
resource "google_cloud_run_v2_service" "mmg_api" {
  project  = var.project_id
  name     = "mmg-api"
  location = var.region

  deletion_protection = false

  template {
    service_account                  = google_service_account.mmg_api.email
    timeout                          = "3600s"
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }

    containers {
      image = var.cloud_run_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        cpu_idle          = false # ADK ストリーミングのため CPU 常時割り当て
        startup_cpu_boost = true
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 6
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "FIREBASE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "VERTEX_AI_LOCATION"
        value = var.vertex_ai_location
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.vertex_ai_location
      }
      env {
        name  = "AGENT_RUNTIME_LOCATION"
        value = var.agent_runtime_location
      }
      env {
        name  = "AGENT_ENGINE_RESOURCE_NAME"
        value = var.agent_engine_resource_name
      }
      env {
        name  = "AGENT_ENGINE_ID"
        value = var.agent_engine_id
      }
      env {
        name  = "FRONTEND_ORIGIN"
        value = var.frontend_origin
      }
    }
  }

  # イメージは CI（GitHub Actions）が deploy するため、TF はタグ差分を無視して上書きしない。
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.mmg_api,
  ]
}

# アプリ自身が Firebase ID Token で認証するため、Cloud Run 自体は未認証呼び出しを許可する。
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.mmg_api.location
  name     = google_cloud_run_v2_service.mmg_api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
