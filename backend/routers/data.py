"""
Data Router — /api/data

全データモデルを横断して閲覧するための汎用ルーター。

設計方針（暫定・薄く軽く）:
- フロントエンドはオントロジー変更時に大改修したくないため、表示の整形はせず
  「コレクション一覧」と「任意ビューのドキュメント群」を素のまま返す。
  オントロジー追加時は、本ファイルの VIEWS にエントリを1つ足すだけで済む。
- 編集はチャットの AI エージェントに委ねるため、本ルーターは読み取り専用。
- 個別レコードが怪しいときは lineage 逆引き（by-entity）で元データ・加工根拠を辿る。

テナント分離は SpaceContext（col/doc）経由でのみアクセスすることで構造的に担保する。
ネスト（contacts は events/{eid}/batches/{bid}/contacts/{cid}）や横断走査は
collection_group を使わず、必ず SpaceContext 経由でトラバースする。
"""

import logging
from typing import Any, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_space_context
from routers.integration import _format_lineage_report
from segmentation import _iter_contacts
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


# ── データビューのレジストリ ──────────────────────────────────────────────────
# 各ビューは「スペース内のドキュメント dict を列挙する関数」を持つ。
# オントロジー追加時はここに1エントリ足すだけ（フロントは無修正）。

def _list_collection(name: str) -> Callable[[SpaceContext], Iterator[dict]]:
    """トップレベル（またはパス固定の）コレクションを素直に列挙する lister を返す。"""
    def lister(space: SpaceContext) -> Iterator[dict]:
        for doc in space.col(name).get():
            yield doc.to_dict()
    return lister


def _list_contacts(space: SpaceContext) -> Iterator[dict]:
    """ハウスリスト（全イベント・全バッチ横断のコンタクト）を列挙する。"""
    # _iter_contacts はスペース前置済み ScopedClient を受け取り、幽霊doc対策の
    # list_documents() でトラバースする（テナント横断はしない）。
    yield from _iter_contacts(space.scoped_db())


def _list_emails(space: SpaceContext) -> Iterator[dict]:
    """生成済みメール（marketing_runs 配下の emails）を横断列挙する。"""
    for run in space.col("marketing_runs").list_documents():
        for email in space.col(f"marketing_runs/{run.id}/emails").get():
            yield email.to_dict()


# key -> (label, lister)
VIEWS: dict[str, tuple[str, Callable[[SpaceContext], Iterator[dict]]]] = {
    "events":         ("イベント",          _list_collection("events")),
    "contacts":       ("ハウスリスト",      _list_contacts),
    "content_assets": ("コンテンツ",        _list_collection("content_assets")),
    "segments":       ("セグメント",        _list_collection("segments")),
    "marketing_runs": ("メール生成ジョブ",  _list_collection("marketing_runs")),
    "emails":         ("生成メール",        _list_emails),
}


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/collections")
async def list_collections(space: SpaceContext = Depends(get_space_context)):
    """閲覧可能なデータビューの一覧（左メニュー用）を返す。"""
    return {"collections": [{"key": key, "label": label} for key, (label, _) in VIEWS.items()]}


@router.get("/lineage/by-entity/{entity_id}")
async def lineage_by_entity(
    entity_id: str,
    space: SpaceContext = Depends(get_space_context),
):
    """entity_id を生成した data_lineage を逆引きして返す（由来追跡）。

    created_entity_ids は {"events": [...], "contacts": [...]} 形式（dict[str, list]）で
    動的キーのため array_contains が使えない。ハッカソン規模では全走査で十分。
    データ増加時は取り込み時に逆引きインデックスを書く案へ移行する。
    """
    for doc in space.col("data_lineage").get():
        lineage = doc.to_dict()
        created = lineage.get("created_entity_ids", {}) or {}
        if any(entity_id in (ids or []) for ids in created.values()):
            return {"entity_id": entity_id, "report": _format_lineage_report(lineage)}
    return {"entity_id": entity_id, "report": None}


@router.get("/{view_key}")
async def list_view(
    view_key: str,
    space: SpaceContext = Depends(get_space_context),
):
    """指定ビューのドキュメント群を素のまま返す（整形しない＝汎用）。"""
    entry = VIEWS.get(view_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown data view")
    _, lister = entry
    rows: list[dict[str, Any]] = list(lister(space))
    return {"key": view_key, "rows": rows, "count": len(rows)}
