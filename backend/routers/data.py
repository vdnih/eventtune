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

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from config import get_settings
from dependencies import get_space_context
from genai_client import new_client
from metering import record_llm_response
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
    "events": ("イベント", "マスタ", _list_collection("events")),
    "persons": ("ハウスリスト", "マスタ", _list_collection("persons")),
    "accounts": ("企業", "マスタ", _list_collection("accounts")),
    "products": ("製品", "マスタ", _list_collection("products")),
    "contents": ("コンテンツ", "マスタ", _list_collection("contents")),
    "event_attendances": ("イベント参加", "ファクト", _list_collection("event_attendances")),
    "product_interests": ("製品関心", "ファクト", _list_collection("product_interests")),
    "cost_items": ("費用明細", "ファクト", _list_collection("cost_items")),
    "source_records": ("取り込み行（着地）", "取り込み", _list_collection("source_records")),
    "segments": ("セグメント", "分析", _list_collection("segments")),
    "marketing_runs": ("生成ジョブ", "生成", _list_collection("marketing_runs")),
    "deliverables": ("生成成果物", "生成", _list_deliverables),
}


# ── ファクト/参照の表示名エンリッチ ───────────────────────────────────────────
#
# osi_event_marketing_v1.yml の `relationships`（FK 宣言）に対応する FK を（手動で）
# 反映し、フロントが ID しか読めない問題を解消する。ファクト行にマスタの表示名を
# 付与して返す。※YAML は概念モデルで Python からはロードしない。runtime の正典は
# specs.py の REGISTRY / 本 _ENRICH 側であり、両者を YAML と手動で揃える。
# Firestore は物理 JOIN しないため、対象マスタを1回だけ stream して {id: name} 辞書を
# 作り、O(1) 辞書引きで付与する（N+1 なし）。
#
# view_key -> [(fk_field, master_collection, master_name_field, output_field), ...]
_ENRICH: dict[str, list[tuple[str, str, str, str]]] = {
    "event_attendances": [
        ("person_id", "persons", "name", "person_name"),
        ("event_id", "events", "name", "event_name"),
    ],
    "product_interests": [
        ("person_id", "persons", "name", "person_name"),
        ("product_id", "products", "product_name", "product_name"),
    ],
    "cost_items": [
        ("event_id", "events", "name", "event_name"),
    ],
    "persons": [
        ("account_id", "accounts", "account_name", "account_name"),
    ],
    "contents": [
        ("linked_event_id", "events", "name", "event_name"),
    ],
}


def _name_map(space: SpaceContext, collection: str, name_field: str) -> dict[str, str]:
    """マスタコレクションを1回 stream して {doc_id: 表示名} 辞書を作る。"""
    result: dict[str, str] = {}
    for doc in space.col(collection).stream():
        data = doc.to_dict() or {}
        name = data.get(name_field)
        if isinstance(name, str) and name:
            result[doc.id] = name
    return result


def _enrich_rows(
    space: SpaceContext, view_key: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """ファクト/参照行にマスタ表示名を付与する。宣言が無いビューは素通し。"""
    specs = _ENRICH.get(view_key)
    if not specs or not rows:
        return rows
    # 同一マスタは辞書をメモ化して重複 stream を避ける。
    caches: dict[tuple[str, str], dict[str, str]] = {}
    for fk_field, collection, name_field, output_field in specs:
        cache_key = (collection, name_field)
        names = caches.get(cache_key)
        if names is None:
            names = _name_map(space, collection, name_field)
            caches[cache_key] = names
        for row in rows:
            fk = row.get(fk_field)
            if isinstance(fk, str) and fk in names:
                row[output_field] = names[fk]
    return rows


def _collect_rows(space: SpaceContext, view_key: str) -> list[dict[str, Any]]:
    """指定ビューの行を列挙し、可能ならマスタ表示名を付与して返す。"""
    entry = VIEWS.get(view_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown data view")
    _, _group, lister = entry
    rows: list[dict[str, Any]] = list(lister(space))
    return _enrich_rows(space, view_key, rows)


# ── エンドポイント ────────────────────────────────────────────────────────────


@router.get("/collections")
async def list_collections(space: SpaceContext = Depends(get_space_context)):
    """閲覧可能なデータビューの一覧（左メニュー用）を返す。"""
    return {
        "collections": [
            {"key": key, "label": label, "group": group} for key, (label, group, _) in VIEWS.items()
        ]
    }


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


def _strip_vectors(row: dict[str, Any]) -> dict[str, Any]:
    """AI サマリのプロンプト用に埋め込みベクトル（*_vector）を除外する。"""
    return {k: v for k, v in row.items() if not k.endswith("_vector")}


_SUMMARY_MAX_ROWS = 40


async def _generate_summary(
    space: SpaceContext, view_key: str, label: str, rows: list[dict[str, Any]]
) -> str:
    """ビューの行から自然言語サマリを生成する（失敗時は空文字）。"""
    sample = [_strip_vectors(r) for r in rows[:_SUMMARY_MAX_ROWS]]
    body = json.dumps(sample, ensure_ascii=False, default=str)
    prompt = (
        f"次は「{label}」テーブルのデータ（全{len(rows)}件中の先頭サンプル）です。\n"
        "マーケティング担当者向けに、このテーブルが何を表すか・件数規模・"
        "目立つ傾向や偏り（多い値・分布など）を日本語で2〜3文に要約してください。"
        "推測は避け、データから読み取れる事実のみを述べること。\n\n"
        f"【データ(JSON)】\n{body}\n\n【要約】"
    )
    try:
        model = get_settings().model_ingestion
        response = await new_client().aio.models.generate_content(model=model, contents=prompt)
        record_llm_response(space, model, response)
        return (response.text or "").strip()
    except Exception:
        logger.exception("data summary generation failed (view=%s)", view_key)
        return ""


@router.get("/{view_key}/summary")
async def view_summary(
    view_key: str,
    refresh: bool = False,
    space: SpaceContext = Depends(get_space_context),
):
    """ビューの AI サマリ文章を返す。オンデマンド生成し Firestore にキャッシュする。

    refresh=false かつキャッシュありなら再利用。生成は LLM 呼び出しのため課金対象。
    """
    entry = VIEWS.get(view_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown data view")
    label = entry[0]
    cache_ref = space.doc(f"data_summaries/{view_key}")

    if not refresh:
        cached = cache_ref.get()
        if cached.exists:
            data = cached.to_dict() or {}
            if data.get("text"):
                return {"key": view_key, "cached": True, **data}

    rows = _collect_rows(space, view_key)
    text = await _generate_summary(space, view_key, label, rows)
    payload = {
        "text": text,
        "row_count": len(rows),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    cache_ref.set(payload)
    return {"key": view_key, "cached": False, **payload}


@router.get("/{view_key}")
async def list_view(
    view_key: str,
    space: SpaceContext = Depends(get_space_context),
):
    """指定ビューのドキュメント群を返す。ファクトはマスタ表示名を付与（汎用）。"""
    rows = _collect_rows(space, view_key)
    return {"key": view_key, "rows": rows, "count": len(rows)}
