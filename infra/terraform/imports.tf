# ブラウンフィールド: 既存の手作業リソースを Terraform に取り込む（宣言的 import ブロック）。
# 取り込み後 `terraform plan` が差分なし（no-op）になることが正典化の合格基準。
# import 完了後はこのファイルを削除/コメントアウトしてよい。

import {
  to = google_firebase_project.default
  id = var.project_id
}

import {
  to = google_firebase_web_app.default
  # apps/{appId}。実際の Web アプリ ID（1:974233950009:web:75bbffb7465a269bf8bae8 の web:以降）に置換。
  # 取得: firebase apps:list WEB  もしくは コンソール
  id = "projects/${var.project_id}/webApps/REPLACE_WITH_WEB_APP_ID"
}

import {
  to = google_firestore_database.default
  id = "projects/${var.project_id}/databases/(default)"
}

import {
  to = google_storage_bucket.default
  id = "${var.project_id}/${var.project_id}.firebasestorage.app"
}

# 既に有効な API は google_project_service の apply が冪等なので import 不要。
