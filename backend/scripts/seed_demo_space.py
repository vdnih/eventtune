"""
ハッカソン提出デモ用の新規スペースを作成し、sample_data/demo_data/ 配下のファイル一式を
実際のデータ統合パイプライン（understand_batch → process_batch）で取り込むスクリプト。

HTTPルータ（backend/routers/spaces.py, backend/routers/integration.py）と認証層を経由せず、
Firebase Admin SDK と backend の関数を直接呼び出すことで、開発者のログインセッションなしに
スペース作成〜取り込み完走までを検証・再現できるようにする。

使い方:
    cd backend
    uv run python scripts/seed_demo_space.py --email you@example.com [--space-name "..."]

前提: ADC（ローカルなら `gcloud auth application-default login`）と backend/.env、
      および sample_data/demo_data/ が repo ルート直下に存在すること。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

import firebase_admin  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from firebase_admin import auth, firestore  # noqa: E402

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)

_ENV_PATH = os.path.join(_BACKEND_DIR, ".env")
# main.py と同様、genai.Client() が os.environ から Vertex AI 認証情報を拾えるようにする
# （config.Settings は pydantic-settings 経由で読むだけで os.environ には反映しないため）。
load_dotenv(_ENV_PATH)

from config import Settings  # noqa: E402

_DEMO_DATA_DIR = os.path.join(_REPO_ROOT, "sample_data", "demo_data")

# (バッチ名, ヒント, フォルダ内ファイル名 or 直下ファイル名のリスト)
_BATCHES: list[tuple[str, str, list[str]]] = [
    (
        "プロダクト・コンテンツカタログ",
        "自社プロダクトとマーケティングコンテンツのマスタ一覧です。イベントには紐づきません。",
        ["product_catalog.txt", "content_catalog.txt"],
    ),
    (
        "2026年度上期 イベント計画書",
        "2026年上期に実施予定の3イベントの計画書です。各イベントの概要・予算・KPI目標を含みます。",
        ["annual_plan_2026_h1.txt"],
    ),
    (
        "Cloud Ops Summit 2026(展示会)",
        "Cloud Ops Summit 2026 の接客リード・展示会概要・費用実績・実施結果報告・アンケート集計です。",
        [
            "01_cloud_ops_summit_2026/leads.csv",
            "01_cloud_ops_summit_2026/展示会概要メモ.docx",
            "01_cloud_ops_summit_2026/費用実績.xlsx",
            "01_cloud_ops_summit_2026/実施結果報告.pptx",
            "01_cloud_ops_summit_2026/survey.txt",
        ],
    ),
    (
        "SREのためのインシデント対応高速化ウェビナー(セミナー)",
        "ウェビナーの概要・参加者一覧・アンケート集計です。",
        [
            "02_sre_incident_webinar_2026/overview.txt",
            "02_sre_incident_webinar_2026/attendees.csv",
            "02_sre_incident_webinar_2026/survey.txt",
        ],
    ),
    (
        "CTO限定 クラウド運用コスト円卓会議(プライベートイベント)",
        "招待制イベントの概要と招待者一覧です。",
        [
            "03_cto_roundtable_2026/overview.txt",
            "03_cto_roundtable_2026/guests.csv",
        ],
    ),
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_files(rel_paths: list[str]) -> list[tuple[str, bytes]]:
    loaded = []
    for rel in rel_paths:
        path = os.path.join(_DEMO_DATA_DIR, rel)
        with open(path, "rb") as f:
            loaded.append((os.path.basename(rel), f.read()))
    return loaded


def _create_space(db, uid: str, email: str, name: str, description: str) -> str:
    now = _now_iso()
    space_id = f"space_{uuid.uuid4().hex[:12]}"
    space_doc = {
        "space_id": space_id,
        "name": name,
        "plan": "free",
        "owner_uid": uid,
        "description": description,
        "created_at": now,
        "updated_at": now,
    }
    member_doc = {
        "user_id": uid,
        "email": email,
        "role": "owner",
        "space_id": space_id,
        "space_name": name,
        "joined_at": now,
    }
    batch = db.batch()
    batch.set(db.document(f"spaces/{space_id}"), space_doc)
    batch.set(db.document(f"spaces/{space_id}/members/{uid}"), member_doc)
    batch.commit()
    return space_id


async def _run_all_batches(space, db) -> None:
    from agents.data_integration_agent import UnderstandError, process_batch, understand_batch
    from space import SpaceContext  # noqa: F401  (型ヒント用途の明示)

    scoped = space.scoped_db()

    for label, hint, rel_paths in _BATCHES:
        print(f"\n=== バッチ: {label} ===")
        files = _load_files(rel_paths)
        existing_events = [
            (doc.to_dict() or {}).get("name", "") for doc in space.col("events").get()
        ]
        existing_events = [n for n in existing_events if n]

        try:
            plan = await understand_batch(files, hint, existing_events, space=space)
        except UnderstandError as e:
            print(f"  [ERROR] understand_batch 失敗: {e}")
            continue

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        space.col("integration_jobs").document(batch_id).set(
            {
                "job_id": batch_id,
                "batch_id": batch_id,
                "filenames": [f for f, _ in files],
                "hint": hint,
                "plan": plan.model_dump(),
                "status": "processing",
                "stage": "",
                "created_at": _now_iso(),
            }
        )

        result = await process_batch(files, batch_id, scoped, plan, space=space)

        space.col("integration_jobs").document(batch_id).update(
            {
                "status": "done",
                "created_entities": result.created_entities,
                "pending_count": result.pending_count,
                "skipped_count": result.skipped_count,
                "report_markdown": result.report_markdown,
            }
        )

        print(f"  batch_id={batch_id}")
        print(f"  created_entities={result.created_entities}")
        print(f"  pending_count={result.pending_count} skipped_count={result.skipped_count}")
        if result.pending_count or result.skipped_count:
            print("  [WARN] pending/skipped が0ではありません。report_markdown を確認してください。")
            print(f"  report_markdown:\n{result.report_markdown}")


def _print_summary(space) -> None:
    print("\n=== 最終集計 ===")
    collections = [
        "accounts",
        "persons",
        "events",
        "products",
        "contents",
        "event_attendances",
        "product_interests",
        "cost_items",
    ]
    for c in collections:
        n = sum(1 for _ in space.col(c).stream())
        print(f"  {c:20s} {n:>4d}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="スペースオーナーにする Firebase ユーザーのメールアドレス")
    parser.add_argument("--space-name", default="クラウドフォージ デモ", help="作成するスペース名")
    parser.add_argument(
        "--description",
        default="ハッカソン提出デモ用スペース(クラウド運用DX SaaS シナリオ)",
        help="スペースの説明",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=_ENV_PATH)
    firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    db = firestore.client()

    try:
        user = auth.get_user_by_email(args.email)
    except Exception as e:
        print(f"[ERROR] ユーザーが見つかりません: {args.email} ({e})")
        sys.exit(1)

    space_id = _create_space(db, user.uid, args.email, args.space_name, args.description)
    print(f"スペース作成: {space_id}(owner={args.email}、プロジェクト={settings.firebase_project_id})")

    from space import SpaceContext

    space = SpaceContext(space_id=space_id, uid=user.uid, role="owner", db=db)

    asyncio.run(_run_all_batches(space, db))
    _print_summary(space)

    print(f"\n完了。space_id={space_id}")


if __name__ == "__main__":
    main()
