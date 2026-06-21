"""
Segmentation — 個別カスタマイズの第1段階（コンタクト → バケット分類）

施策向けに定義された Segment（軸・バケット・criteria）に従い、対象コンタクトを
バケットへ割り当てる。割り当ては Auditable AI に従い、必ず reason を残す。

責務境界（[[feedback_ai_python_boundary]]）:
- 構造化フィールド（interested_products / engagement_level / stage）だけで自明に決まる
  軸は **決定論Python** で割り当てる（LLM不使用・ゼロトークン）。
- extracted_challenge / notes 等の意味判断が要る場合のみ **軽量モデル**で判別する。
  トークン節約のため、複数コンタクトを1回の呼び出しでまとめて分類し、出力は
  contact_id/bucket/reason のみに絞る。

オーケストレーション（いつ分類するか）はエージェントの判断。本モジュールは「分類の実計算」
という業務ロジックを決定論的・非ブラックボックスに担う。
"""

from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from metering import record_llm_response
from ontology import Product, Segment, SegmentAssignment
from space import SpaceContext

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite"
_BATCH_SIZE = 20  # 1回のLLM呼び出しで分類するコンタクト数


# ── コンタクト走査 ───────────────────────────────────────────────────────────

def _iter_contacts(db: Any, event_id: Optional[str] = None) -> Iterator[dict]:
    """スコープ内のコンタクトを列挙する（db はスペース前置済み ScopedClient）。

    event_id 指定時はそのイベントのみ。未指定なら全イベント横断。
    batches/{bid} は実体化されない幽霊ドキュメントのため list_documents() で列挙する
    （marketing_agent.get_event_contacts と同じ理由）。
    """
    if event_id:
        event_ids = [event_id]
    else:
        event_ids = [d.id for d in db.collection("events").list_documents()]

    for eid in event_ids:
        batches = db.collection(f"events/{eid}/batches").list_documents()
        for batch in batches:
            coll = db.collection(f"events/{eid}/batches/{batch.id}/contacts").get()
            for c in coll:
                yield c.to_dict()


# ── 決定論的割り当て（自明な軸のみ） ─────────────────────────────────────────

def _deterministic_bucket(
    segment: Segment, contact: dict
) -> Optional[tuple[str, str, dict[str, str]]]:
    """構造化フィールドだけで自明に決まる場合に (bucket, reason, signals) を返す。

    対応する自明ケース: 単一軸で、その軸が「関心製品」を表す場合。
    interested_products から決まるため LLM 不要。判定できない場合は None を返し、
    呼び出し側が軽量LLMにフォールバックする。
    """
    if len(segment.axes) != 1:
        return None

    axis = segment.axes[0]
    # 製品関心軸の検出（軸名に「製品」「プロダクト」を含む）
    if not any(kw in axis.name for kw in ("製品", "プロダクト", "product", "Product")):
        return None

    interested = contact.get("interested_products", []) or []
    signals = {"interested_products": ", ".join(map(str, interested))}

    # 軸の各値（バケット）名に、関心製品名が含まれる最初のものへ割り当てる
    for value in axis.values:
        for prod in interested:
            prod_str = prod.value if isinstance(prod, Product) else str(prod)
            if prod_str and prod_str in value:
                return value, f"関心製品「{prod_str}」が軸値に合致", signals

    # 関心製品が無い/合致しない場合、「未特定」系のバケットがあればそこへ
    for value in axis.values:
        if any(kw in value for kw in ("未特定", "その他", "不明", "なし")):
            return value, "関心製品が特定できず既定バケットへ", signals

    return None


# ── 軽量LLMによるバッチ分類 ──────────────────────────────────────────────────

class _OneAssignment(BaseModel):
    contact_id: str
    bucket: str
    reason: str


class _BatchResult(BaseModel):
    assignments: list[_OneAssignment]


def _classify_batch(
    space: SpaceContext, segment: Segment, contacts: list[dict]
) -> dict[str, tuple[str, str]]:
    """コンタクト群を1回のLLM呼び出しでまとめてバケットへ分類する。

    Returns: { contact_id: (bucket, reason) }。トークン節約のため入力は判別に要る
    最小フィールドのみ、出力は contact_id/bucket/reason のみ。
    """
    axes_desc = "\n".join(
        f"- {ax.name}: {' / '.join(ax.values)}" for ax in segment.axes
    )
    slim = [
        {
            "contact_id": c.get("contact_id", ""),
            "job_title": c.get("job_title", ""),
            "engagement_level": c.get("engagement_level"),
            "interested_products": c.get("interested_products", []),
            "extracted_challenge": c.get("extracted_challenge", ""),
            "notes": c.get("notes", ""),
        }
        for c in contacts
    ]
    prompt = f"""\
施策「{segment.name}」（目的: {segment.purpose}）のためにコンタクトを分類します。

【セグメント軸】
{axes_desc}

【割り当て基準】
{segment.criteria}

【バケット一覧（必ずこの中から1つを選ぶ）】
{chr(10).join(f"- {b}" for b in segment.buckets)}

以下の各コンタクトを、最も適切なバケットへ分類してください。
bucket は必ず上記バケット一覧の文字列のいずれかと完全一致させること。
reason は判定根拠を20〜40字で簡潔に。

【コンタクト】
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
        out[a.contact_id] = (bucket, reason)
    return out


# ── 公開エントリ ─────────────────────────────────────────────────────────────

def assign_contacts_to_segment(
    db: Any,
    space: SpaceContext,
    segment: Segment,
    event_id: Optional[str] = None,
) -> dict:
    """対象コンタクトを Segment のバケットへ割り当て、Firestore に保存する。

    db: スペース前置済み ScopedClient。
    戻り値: { total, by_bucket: {bucket: count}, llm_contacts: int }（会話報告用サマリ）。
    """
    contacts = list(_iter_contacts(db, event_id))
    fallback = segment.buckets[-1] if segment.buckets else "未分類"

    assignments: list[SegmentAssignment] = []
    undecided: list[dict] = []

    # 1) 決定論で決まるものを先に処理
    for c in contacts:
        cid = c.get("contact_id", "")
        if not cid:
            continue
        det = _deterministic_bucket(segment, c)
        if det is not None:
            bucket, reason, signals = det
            assignments.append(SegmentAssignment(
                contact_id=cid, segment_id=segment.segment_id,
                bucket=bucket, reason=reason, source_signals=signals,
            ))
        else:
            undecided.append(c)

    # 2) 残りを軽量LLMでバッチ分類
    llm_contacts = 0
    for i in range(0, len(undecided), _BATCH_SIZE):
        chunk = undecided[i:i + _BATCH_SIZE]
        try:
            decided = _classify_batch(space, segment, chunk)
        except Exception:
            logger.exception("segment batch classify failed: segment=%s", segment.segment_id)
            decided = {}
        for c in chunk:
            cid = c.get("contact_id", "")
            bucket, reason = decided.get(cid, (fallback, "分類失敗→既定バケット"))
            assignments.append(SegmentAssignment(
                contact_id=cid, segment_id=segment.segment_id,
                bucket=bucket, reason=reason,
                source_signals={"extracted_challenge": c.get("extracted_challenge", "")},
            ))
            llm_contacts += 1

    # 3) 保存（segments/{sid}/assignments/{cid}）
    by_bucket: dict[str, int] = {}
    for a in assignments:
        db.collection(f"segments/{segment.segment_id}/assignments").document(
            a.contact_id
        ).set(a.model_dump())
        by_bucket[a.bucket] = by_bucket.get(a.bucket, 0) + 1

    summary = {"total": len(assignments), "by_bucket": by_bucket, "llm_contacts": llm_contacts}
    logger.info("segment assigned: segment=%s summary=%s", segment.segment_id, summary)
    return summary
