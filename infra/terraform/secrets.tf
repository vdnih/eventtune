# 現状 config.py が読む値はいずれも秘匿情報ではない（プロジェクトID・ロケーション・Agent Engine ID・
# 配信オリジン）。よって Cloud Run の平文 env で渡し、Secret Manager は使わない（ADR-012・過剰回避）。
# SA には secretmanager.secretAccessor を付与済みなので、将来 API キー等の真の秘密が必要になったら
# 以下のテンプレートを有効化し、cloud_run.tf の env を value_source.secret_key_ref に切り替える。
#
# resource "google_secret_manager_secret" "example" {
#   project   = var.project_id
#   secret_id = "example-api-key"
#   replication { auto {} }
# }
#
# resource "google_secret_manager_secret_version" "example" {
#   secret      = google_secret_manager_secret.example.id
#   secret_data = var.example_api_key
# }
