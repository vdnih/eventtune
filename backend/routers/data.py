"""
Data Router — /api/data

全データモデルを横断して閲覧するための汎用ルーター。

設計方針（薄く軽く）:
- フロントエンドはオントロジー変更時に大改修したくないため、表示の整形はせず
  「コレクション一覧」と「任意ビューのドキュメント群」を素のまま返す。
  オントロジー追加時は、本ファイルの VIEWS にエントリを1つ足すだけで済む。
- 編集はチャットの AI エージェントに委ねるため、本ルーターは読み取り専用。
- ADR-008: contacts → persons, content_assets → contents に変更。
  lineage 逆引きは persons.source_job_id → integration_jobs への直接参照。
"""

import logging
from typing import Any, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


# ── データビューのレジストリ ──────────────────────────────────────────────────

def _list_collection(name: str) -> Callable[[SpaceContext], Iterator[dict]]:
    def lister(space: SpaceContext) -> Iterator[dict]:
        for doc in space.col(name).stream():
            data = doc.to_dict()
            if data:
                yield data
    return lister


def _list_deliverables(space: SpaceContext) -> Iterator[dict]:
    """生成済み成果物（marketing_runs 配下の deliverables）を横断列挙する。"""
    for run in space.col("marketing_runs").list_documents():
        for d in space.col(f"marketing_runs/{run.id}/deliverables").stream():
            data = d.to_dict()
            if data:
                yield data


# key -> (label, group, lister)
VIEWS: dict[str, tuple[str, str, Callable[[SpaceContext], Iterator[dict]]]] = {
    "events":             ("イベント",      "マスタ",   _list_collection("events")),
    "persons":            ("ハウスリスト",  "マスタ",   _list_collection("persons")),
    "accounts":           ("企業",          "マスタ",   _list_collection("accounts")),
    "products":           ("製品",          "マスタ",   _list_collection("products")),
    "contents":           ("コンテンツ",    "マスタ",   _list_collection("contents")),
    "event_attendances":  ("イベント参加",  "ファクト", _list_collection("event_attendances")),
    "product_interests":  ("製品関心",      "ファクト", _list_collection("product_interests")),
    "cost_items":         ("費用明細",      "ファクト", _list_collection("cost_items")),
    "segments":           ("セグメント",    "分析",     _list_collection("segments")),
    "marketing_runs":     ("生成ジョブ",    "生成",     _list_collection("marketing_runs")),
    "deliverables":       ("生成成果物",    "生成",     _list_deliverables),
}


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/collections")
async def list_collections(space: SpaceContext = Depends(get_space_context)):
    """閲覧可能なデータビューの一覧（左メニュー用）を返す。"""
    return {"collections": [
        {"key": key, "label": label, "group": group}
        for key, (label, group, _) in VIEWS.items()
    ]}


@router.get("/lineage/by-entity/{entity_id}")
async def lineage_by_entity(
    entity_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """entity_id（person_id 等）の由来を integration_jobs から直接参照して返す。

    ADR-008: persons.source_job_id → integration_jobs への直接参照で O(1) ルックアップ。
    """
    # persons / accounts / event_attendances / product_interests を検索
    for collection in ("persons", "accounts", "event_attendances", "product_interests"):
        doc = space.doc(f"{collection}/{entity_id}").get()
        if doc.exists:
            data = doc.to_dict() or {}
            job_id = data.get("source_job_id")
            if job_id:
                job_doc = space.doc(f"integration_jobs/{job_id}").get()
                if job_doc.exists:
                    return {"entity_id": entity_id, "job": job_doc.to_dict()}
            return {"entity_id": entity_id, "job": None}

    return {"entity_id": entity_id, "job": None}


@router.get("/{view_key}")
async def list_view(
    view_key: str,
    space: SpaceContext = Depends(get_space_context),
):
    """指定ビューのドキュメント群を素のまま返す（整形しない＝汎用）。"""
    entry = VIEWS.get(view_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown data view")
    _, _group, lister = entry
    rows: list[dict[str, Any]] = list(lister(space))
    return {"key": view_key, "rows": rows, "count": len(rows)}
