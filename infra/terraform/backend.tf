# リモートステート（GCS）。
# このバケットだけは鶏卵問題のため事前に1回だけ手動作成する:
#   gsutil mb -l asia-northeast1 -p marketing-mail-generator gs://marketing-mail-generator-tfstate
#   gsutil versioning set on gs://marketing-mail-generator-tfstate
terraform {
  backend "gcs" {
    bucket = "marketing-mail-generator-tfstate"
    prefix = "infra"
  }
}
