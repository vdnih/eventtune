"""
Segmentation — 個別カスタマイズの第1段階（Person → バケット分類）

施策向けに定義された Segment（軸・バケット・criteria）に従い、対象 Person を
バケットへ割り当てる。割り当ては Auditable AI に従い、必ず reason を残す。

責務境界:
- 構造化フィールド（ProductInterest / engagement_level / stage）だけで自明に決まる
  軸は決定論 Python で割り当てる（LLM 不使用）。
- extracted_challenge / notes 等の意味判断が要る場合のみ軽量モデルで判別する。
  トークン節約のため、複数 Person を1回の呼び出しでまとめて分類する。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from metering import record_llm_response
from ontology import Segment, SegmentAssignment, SegmentSnapshot
from space import SpaceContext

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite"
_BATCH_SIZE = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ── Person 走査 ──────────────────────────────────────────────────────────────

def _iter_persons(space: SpaceContext, event_id: Optional[str] = None) -> Iterator[dict]:
    """スペース内の Person を列挙する（ADR-008 フラットスキーマ）。

    event_id 指定時は event_attendances → persons の順で絞り込む。
    未指定なら persons を直接 stream する。
    """
    if event_id:
        att_docs = space.col("event_attendances").where("event_id", "==", event_id).stream()
        person_ids = [a.to_dict().get("person_id") for a in att_docs]
        for pid in (p for p in person_ids if p):
            doc = space.doc(f"persons/{pid}").get()
            if doc.exists:
                yield doc.to_dict()
    else:
        for doc in space.col("persons").stream():
            data = doc.to_dict()
            if data:
                yield data


def _get_product_interests(space: SpaceContext, person_id: str) -> tuple[list[str], list[str]]:
    """person_id に紐づく (product_id リスト, product_name リスト) を返す。

    製品名は products マスタ（データ駆動）から解決する。旧 ProductCode のハードコード
    マッピングは撤去し、取り込みで生成された Product.product_name を参照する。
    """
    docs = space.col("product_interests").where("person_id", "==", person_id).stream()
    product_ids = [d.to_dict().get("product_id", "") for d in docs if d.to_dict()]
    names: list[str] = []
    for pid in product_ids:
        if not pid:
            continue
        pdoc = space.doc(f"products/{pid}").get()
        if pdoc.exists:
            name = (pdoc.to_dict() or {}).get("product_name", "")
            if name:
                names.append(name)
    return product_ids, names


# ── 決定論的割り当て ──────────────────────────────────────────────────────────

def _deterministic_bucket(
    space: SpaceContext, segment: Segment, person: dict
) -> Optional[tuple[str, str, dict[str, str]]]:
    """構造化フィールドだけで自明に決まる場合に (bucket, reason, signals) を返す。

    対応する自明ケース: 単一軸で、その軸が「関心製品」を表す場合。
    """
    if len(segment.axes) != 1:
        return None

    axis = segment.axes[0]
    if not any(kw in axis.name for kw in ("製品", "プロダクト", "product", "Product")):
        return None

    person_id = person.get("person_id", "")
    product_ids, product_names = _get_product_interests(space, person_id)
    signals = {"product_ids": ", ".join(product_ids)}

    for value in axis.values:
        for pname in product_names:
            if pname and pname in value:
                return value, f"関心製品「{pname}」が軸値に合致", signals

    for value in axis.values:
        if any(kw in value for kw in ("未特定", "その他", "不明", "なし")):
            return value, "関心製品が特定できず既定バケットへ", signals

    return None


# ── 軽量 LLM によるバッチ分類 ────────────────────────────────────────────────

class _OneAssignment(BaseModel):
    person_id: str
    bucket: str
    reason: str


class _BatchResult(BaseModel):
    assignments: list[_OneAssignment]


def _classify_batch(
    space: SpaceContext, segment: Segment, persons: list[dict]
) -> dict[str, tuple[str, str]]:
    """Person 群を1回の LLM 呼び出しでまとめてバケットへ分類する。

    Returns: { person_id: (bucket, reason) }
    """
    axes_desc = "\n".join(
        f"- {ax.name}: {' / '.join(ax.values)}" for ax in segment.axes
    )
    slim = [
        {
            "person_id": p.get("person_id", ""),
            "job_title": p.get("job_title", ""),
            "engagement_level": p.get("engagement_level"),
            "extracted_challenge": p.get("extracted_challenge", ""),
            "notes": p.get("notes", ""),
        }
        for p in persons
    ]
    prompt = f"""\
施策「{segment.name}」（目的: {segment.purpose}）のためにコンタクトを分類します。

【セグメント軸】
{axes_desc}

【割り当て基準】
{segment.criteria}

【バケット一覧（必ずこの中から1つを選ぶ）】
{chr(10).join(f"- {b}" for b in segment.buckets)}

以下の各 Person を、最も適切なバケットへ分類してください。
bucket は必ず上記バケット一覧の文字列のいずれかと完全一致させること。
reason は判定根拠を20〜40字で簡潔に。

【Person】
{slim}
"""
    client = genai.Client()
    response = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BatchResult,
        ),
    )
    record_llm_response(space, _MODEL, response)
    result = _BatchResult.model_validate_json(response.text)

    out: dict[str, tuple[str, str]] = {}
    valid = set(segment.buckets)
    fallback = segment.buckets[-1] if segment.buckets else "未分類"
    for a in result.assignments:
        bucket = a.bucket if a.bucket in valid else fallback
        reason = a.reason if a.bucket in valid else f"AI出力'{a.bucket}'が一覧外→既定"
        out[a.person_id] = (bucket, reason)
    return out


# ── 公開エントリ ─────────────────────────────────────────────────────────────

def assign_contacts_to_segment(
    space: SpaceContext,
    segment: Segment,
    event_id: Optional[str] = None,
) -> dict:
    """対象 Person を Segment のバケットへ割り当て、Firestore にスナップショットとして保存する。

    戻り値: { snapshot_id, total, by_bucket, llm_persons }
    """
    persons = list(_iter_persons(space, event_id))
    fallback = segment.buckets[-1] if segment.buckets else "未分類"
    snapshot_id = _new_id("snap_")

    assignments: list[SegmentAssignment] = []
    undecided: list[dict] = []

    # 1) 決定論で決まるものを先に処理
    for p in persons:
        pid = p.get("person_id", "")
        if not pid:
            continue
        det = _deterministic_bucket(space, segment, p)
        if det is not None:
            bucket, reason, signals = det
            assignments.append(SegmentAssignment(
                person_id=pid,
                segment_id=segment.segment_id,
                snapshot_id=snapshot_id,
                space_id=space.space_id,
                bucket=bucket,
                reason=reason,
                source_signals=signals,
            ))
        else:
            undecided.append(p)

    # 2) 残りを軽量 LLM でバッチ分類
    llm_persons = 0
    for i in range(0, len(undecided), _BATCH_SIZE):
        chunk = undecided[i:i + _BATCH_SIZE]
        try:
            decided = _classify_batch(space, segment, chunk)
        except Exception:
            logger.exception("segment batch classify failed: segment=%s", segment.segment_id)
            decided = {}
        for p in chunk:
            pid = p.get("person_id", "")
            bucket, reason = decided.get(pid, (fallback, "分類失敗→既定バケット"))
            assignments.append(SegmentAssignment(
                person_id=pid,
                segment_id=segment.segment_id,
                snapshot_id=snapshot_id,
                space_id=space.space_id,
                bucket=bucket,
                reason=reason,
                source_signals={"extracted_challenge": p.get("extracted_challenge", "")},
            ))
            llm_persons += 1

    # 3) スナップショット下に保存（segments/{sid}/snapshots/{snap_id}/assignments/{pid}）
    by_bucket: dict[str, int] = {}
    for a in assignments:
        space.col(
            f"segments/{segment.segment_id}/snapshots/{snapshot_id}/assignments"
        ).document(a.person_id).set(a.model_dump())
        by_bucket[a.bucket] = by_bucket.get(a.bucket, 0) + 1

    snap = SegmentSnapshot(
        snapshot_id=snapshot_id,
        segment_id=segment.segment_id,
        space_id=space.space_id,
        version=_now_iso(),
        by_bucket=by_bucket,
        created_at=_now_iso(),
    )
    space.col(f"segments/{segment.segment_id}/snapshots").document(snapshot_id).set(
        snap.model_dump()
    )

    summary = {
        "snapshot_id": snapshot_id,
        "total": len(assignments),
        "by_bucket": by_bucket,
        "llm_persons": llm_persons,
    }
    logger.info("segment assigned: segment=%s summary=%s", segment.segment_id, summary)
    return summary
