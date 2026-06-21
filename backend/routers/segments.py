"""
Segments Router — /api/segments

セグメント（施策向けの分類軸）と、その割り当て結果・コンテンツパターンを**閲覧/編集**する
薄いRESTを提供する。フローを駆動するのは MarketingAgent（define_segment / assign_segment /
generate_patterns / run_assembly ツール）であり、ここは人間が成果物を後追い・介入するための窓口。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_space_context
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/segments", tags=["segments"])


@router.get("")
async def list_segments(space: SpaceContext = Depends(get_space_context)):
    """登録済みセグメントの一覧を返す。"""
    docs = space.col("segments").get()
    segments = [d.to_dict() for d in docs]
    segments.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return {"segments": segments, "count": len(segments)}


@router.get("/{segment_id}")
async def get_segment(segment_id: str, space: SpaceContext = Depends(get_space_context)):
    """セグメント定義と、割り当て結果（根拠つき）を返す。"""
    doc = space.col("segments").document(segment_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Segment not found")
    assignments = [
        a.to_dict() for a in space.col(f"segments/{segment_id}/assignments").get()
    ]
    by_bucket: dict[str, int] = {}
    for a in assignments:
        b = a.get("bucket", "")
        by_bucket[b] = by_bucket.get(b, 0) + 1
    return {
        "segment": doc.to_dict(),
        "assignments": assignments,
        "by_bucket": by_bucket,
        "total": len(assignments),
    }


@router.get("/{segment_id}/patterns")
async def get_patterns(segment_id: str, space: SpaceContext = Depends(get_space_context)):
    """セグメントのバケット別コンテンツパターン一覧を返す（レビュー用）。"""
    docs = space.col(f"segments/{segment_id}/patterns").get()
    patterns = [d.to_dict() for d in docs]
    return {"patterns": patterns, "count": len(patterns)}


class PatternUpdate(BaseModel):
    subject: str
    blocks: list[dict]


@router.put("/{segment_id}/patterns/{bucket}")
async def update_pattern(
    segment_id: str,
    bucket: str,
    body: PatternUpdate,
    space: SpaceContext = Depends(get_space_context),
):
    """生成済みパターンを人間が編集・上書きする（HILの介入窓口）。"""
    ref = space.col(f"segments/{segment_id}/patterns").document(bucket)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Pattern not found")
    ref.update({"subject": body.subject, "blocks": body.blocks})
    return {"segment_id": segment_id, "bucket": bucket, "status": "updated"}
