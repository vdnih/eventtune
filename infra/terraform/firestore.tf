# Firestore データベース「本体」のみ Terraform 管理（既存を import）。
# セキュリティルールとインデックスはアプリ成果物として firebase CLI で配信する
#   firebase deploy --only firestore:rules,firestore:indexes
# （Terraform では持たない＝二重管理の回避。ADR-012）
resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.enabled]
}
