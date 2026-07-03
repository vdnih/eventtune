variable "project_id" {
  description = "GCP / Firebase プロジェクト ID"
  type        = string
  default     = "marketing-mail-generator"
}

variable "project_number" {
  description = "GCP プロジェクト番号（Cloud Run 確定 URL や WIF で使用）"
  type        = string
  default     = "974233950009"
}

variable "region" {
  description = "Cloud Run / Firestore / App Hosting のリージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "vertex_ai_location" {
  description = "Gemini 呼び出しのロケーション"
  type        = string
  default     = "global"
}

variable "agent_runtime_location" {
  description = "Agent Engine（ReasoningEngine）のリージョン"
  type        = string
  default     = "us-central1"
}

# Agent Engine は scripts/provision_agent_engine.py で作成する「コードレスのマネージドランタイム」。
# Terraform の google_vertex_ai_reasoning_engine は package_spec（デプロイ済みADKコード）を前提に
# するため当プロジェクトの使い方と一致しない。よって TF では管理せず、ID だけを変数で受け取り
# Cloud Run の env に注入する（ADR-012）。
variable "agent_engine_resource_name" {
  description = "Agent Engine の resource name（provision スクリプトの出力）"
  type        = string
}

variable "agent_engine_id" {
  description = "Agent Engine の ID（provision スクリプトの出力）"
  type        = string
}

variable "frontend_origin" {
  description = "CORS 許可オリジン（App Hosting の配信 URL or 独自ドメイン）"
  type        = string
}

variable "github_owner" {
  description = "GitHub オーナー（App Hosting / WIF 用）"
  type        = string
  default     = "vdnih"
}

variable "github_repo" {
  description = "GitHub リポジトリ名"
  type        = string
  default     = "eventtune"
}

variable "storage_location" {
  description = "Firebase 既定バケットのロケーション（import 一致のため実値に合わせる）"
  type        = string
  default     = "asia-northeast1"
}

variable "cloud_run_image" {
  description = "初期 apply 用のプレースホルダ。実イメージは CI（GitHub Actions）が deploy する"
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}
