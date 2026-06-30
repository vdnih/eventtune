# Firebase Authentication（Google Sign-In）は現状 Firebase 管理（firebase.json の auth ブロック /
# コンソール）で稼働しており、OAuth クライアントも Firebase が自動管理する。
# これを Identity Platform リソースとして Terraform 管理すると client_id/secret の二重管理や
# Firebase 管理設定との競合が起き、得られる利点に対しコストが高い（ADR-012 で「Auth は
# Terraform 管理しない」と決定）。
#
# 将来 Identity Platform へ寄せる場合は以下を有効化する:
#
# resource "google_identity_platform_config" "default" {
#   project = var.project_id
# }
#
# resource "google_identity_platform_default_supported_idp_config" "google" {
#   project       = var.project_id
#   idp_id        = "google.com"
#   client_id     = var.google_oauth_client_id
#   client_secret = var.google_oauth_client_secret # 変数経由・tfvars に平文で置かない
#   enabled       = true
# }
