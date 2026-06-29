"""
指定スペースの「取り込みデータ」を削除して、再取り込みできるクリーンな状態へ戻すスクリプト。

データ形式を変えた（例: ADR-011 の全UUID主キー化・EventAttendance 再編）あとに、
旧形式で残った Firestore のエンティティ群を一掃するために使う。一回限りの運用ツール。

削除するのは「取り込み・解釈の産物」だけ。スペースそのものや認証・課金は触らない:
  削除: persons / product_interests / accounts / products / events / contents /
        event_attendances / segments / integration_jobs
  保持: members（メンバーシップ/認証） / usage（クレジット課金） / threads（チャット履歴）
        および spaces/{space_id} ドキュメント本体

フラットスキーマ（ADR-008）前提なので、各コレクションはトップレベルのドキュメント集合。
ドキュメント配下のサブコレクションは想定しない（あっても本スクリプトは消さない）。

使い方:
    # まず件数だけ確認（削除はしない）
    uv run python scripts/reset_space_ingestion_data.py space_8b33f288e0f1 --dry-run

    # 実削除（対話確認あり）
    uv run python scripts/reset_space_ingestion_data.py space_8b33f288e0f1

    # 対話確認をスキップ（CI/スクリプト用）
    uv run python scripts/reset_space_ingestion_data.py space_8b33f288e0f1 --force

前提: ADC（ローカルなら `gcloud auth application-default login`）と backend/.env。
"""

import argparse
import os
import sys

import firebase_admin
from firebase_admin import firestore

# backend/ をインポートパスに追加して config を再利用
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402

# 削除対象。取り込み・解釈の産物のみ。members / usage / threads は意図的に含めない。
INGESTION_COLLECTIONS = [
    "persons",
    "product_interests",
    "accounts",
    "products",
    "events",
    "contents",
    "event_attendances",
    "segments",
    "integration_jobs",
]

_BATCH_SIZE = 200  # Firestore の commit 上限（500）に対して余裕を持たせる


def _delete_collection(db, path: str) -> int:
    """コレクション配下のドキュメントをバッチ削除し、削除件数を返す。"""
    col = db.collection(path)
    deleted = 0
    while True:
        docs = list(col.limit(_BATCH_SIZE).stream())
        if not docs:
            break
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)
    return deleted


def _count_collection(db, path: str) -> int:
    return sum(1 for _ in db.collection(path).stream())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("space_id", help="対象スペースID（例: space_8b33f288e0f1）")
    parser.add_argument(
        "--dry-run", action="store_true", help="削除せず件数だけ表示する"
    )
    parser.add_argument(
        "--force", action="store_true", help="対話確認をスキップして即削除する"
    )
    args = parser.parse_args()

    settings = get_settings()
    firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    db = firestore.client()

    base = f"spaces/{args.space_id}"

    # スペース存在チェック（誤ったIDで空振りするのを防ぐ）
    if not db.document(base).get().exists:
        print(f"スペースが見つかりません: {base}")
        sys.exit(1)

    # 件数を集計して提示
    counts = {c: _count_collection(db, f"{base}/{c}") for c in INGESTION_COLLECTIONS}
    total = sum(counts.values())

    print(f"対象スペース: {base}（プロジェクト: {settings.firebase_project_id}）")
    print("削除対象コレクション:")
    for c in INGESTION_COLLECTIONS:
        print(f"  {c:20s} {counts[c]:>6d}")
    print(f"  {'合計':20s} {total:>6d}")
    print("保持: members / usage / threads / スペース本体")

    if total == 0:
        print("\n削除対象のドキュメントはありません。")
        return

    if args.dry_run:
        print("\n[dry-run] 削除は実行していません。")
        return

    if not args.force:
        answer = input(f"\n{total} 件を削除します。よろしいですか？ [y/N] ").strip().lower()
        if answer != "y":
            print("中止しました。")
            return

    print()
    for c in INGESTION_COLLECTIONS:
        n = _delete_collection(db, f"{base}/{c}")
        print(f"  削除 {c:20s} {n:>6d}")
    print("\n完了しました。")


if __name__ == "__main__":
    main()
