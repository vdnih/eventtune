"""
semantic_search — appeal_summary / appeal_vector の生成と意味的近接（コサイン類似度）

ADR-008 / SEMANTIC_LAYER §3.5 に基づく。固定ラベルの課題マスタの代わりに、各マスタ
（persons / events / products / contents）へ appeal_summary（監査可能な要約テキスト）と
appeal_vector（その埋め込み）を持たせ、コサイン類似度の総当たりで「この人に合うもの」を引く。

役割分担:
- AI（意味を変える要約）: generate_appeal_summary でマスタの自由文要約を作る。
- 決定論 Python: cosine / find_similar（総当たり）。Firestore のベクトルインデックス・
  find_nearest は使わない（スペース毎に小規模で O(N) 十分）。

非ブロッキング: 埋め込み・要約に失敗しても取り込み自体は止めない（空ベクトル/空要約を返す）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from google import genai
from google.genai import types

from config import get_settings
from genai_client import new_client
from metering import record_llm_response
from space import SpaceContext

logger = logging.getLogger(__name__)

_MODEL_EMBED = "gemini-embedding-001"
# MRL 切り詰め次元。cosine は内部でノルム除算するため未正規化でも一致計算は正しい。
_EMBED_DIM = 768

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = new_client()
    return _client


# ── 埋め込み ───────────────────────────────────────────────────────────────────


async def embed_text(text: str, space: SpaceContext | None = None) -> list[float]:
    """テキストを appeal_vector（list[float]）に埋め込む。空文字・失敗時は [] を返す。"""
    text = (text or "").strip()
    if not text:
        return []
    try:
        response = await _get_client().aio.models.embed_content(
            model=_MODEL_EMBED,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=_EMBED_DIM,
            ),
        )
        if space is not None:
            record_llm_response(space, _MODEL_EMBED, response)
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            return []
        values = getattr(embeddings[0], "values", None) or []
        return [float(v) for v in values]
    except Exception:
        logger.exception("embed_text failed (text len=%d)", len(text))
        return []


def embed_text_sync(text: str, space: SpaceContext | None = None) -> list[float]:
    """embed_text の同期版。空文字・失敗時は [] を返す。

    segmentation 等の同期パス（バケット代表テキストの埋め込み）から使う。意味検索の
    消費側（appeal_vector のコサイン近接）を駆動するために必要。
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        response = _get_client().models.embed_content(
            model=_MODEL_EMBED,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=_EMBED_DIM,
            ),
        )
        if space is not None:
            record_llm_response(space, _MODEL_EMBED, response)
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            return []
        values = getattr(embeddings[0], "values", None) or []
        return [float(v) for v in values]
    except Exception:
        logger.exception("embed_text_sync failed (text len=%d)", len(text))
        return []


# ── 類似度（決定論 Python 総当たり）────────────────────────────────────────────


def cosine(a: list[float], b: list[float]) -> float:
    """コサイン類似度。どちらかが空・ゼロノルムなら 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def find_similar(
    query_vector: list[float],
    candidates: list[tuple[Any, list[float]]],
    top_k: int = 5,
) -> list[tuple[Any, float]]:
    """query_vector に意味的に近い候補を上位 top_k 返す。

    candidates: [(item, vector), ...]。vector が空の候補はスキップする。
    Returns: [(item, score), ...]（score 降順）。
    """
    if not query_vector:
        return []
    scored = [(item, cosine(query_vector, vec)) for item, vec in candidates if vec]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


# ── appeal_summary 生成（AI）───────────────────────────────────────────────────

_SUMMARY_INSTRUCTIONS = {
    "person": (
        "次の人物の情報（複数イベントでの接客履歴・課題感・メモ・関心製品を含む）から、"
        "その人の関心・悩み・文脈を 1〜3 文で要約してください。複数回の接客がある場合は"
        "それらを統合し、繰り返し現れる関心や状況の変化を捉えます。固定の課題ラベルではなく、"
        "何に関心を持ち、どんな状況にあるかを自然文で表現します。"
        "推測の断定は避け、与えられた情報に基づいて書いてください。"
    ),
    "product": (
        "次の製品の情報から、どんな悩みに応え、どんな価値を提供するかを 1〜3 文で要約してください。"
        "実在する説明・効果の範囲に限り、誇張や創作はしないでください（Static Core）。"
    ),
    "content": (
        "次のコンテンツ（素材）の情報から、誰のどんな関心・悩みに応える素材かを 1〜3 文で要約してください。"
        "実在する内容の範囲に限ってください。"
    ),
    "event": (
        "次のイベントの情報から、対象テーマ・登壇内容・提供価値を 1〜3 文で要約してください。"
        "どんな関心を持つ人に合うイベントかが分かるように書いてください。"
    ),
}


def _payload_text(payload: dict) -> str:
    """payload の非空フィールドを「キー: 値」の箇条書きにする。"""
    lines = []
    for k, v in payload.items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            lines.append(f"- {k}: {s}")
    return "\n".join(lines)


async def generate_appeal_summary(
    kind: str,
    payload: dict,
    space: SpaceContext | None = None,
) -> str:
    """マスタの情報から appeal_summary（要約テキスト）を生成する。失敗時は空文字。"""
    instruction = _SUMMARY_INSTRUCTIONS.get(kind)
    body = _payload_text(payload)
    if instruction is None or not body:
        return ""
    prompt = f"{instruction}\n\n【情報】\n{body}\n\n【要約】"
    try:
        _model = get_settings().model_ingestion
        response = await _get_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
        )
        if space is not None:
            record_llm_response(space, _model, response)
        return (response.text or "").strip()
    except Exception:
        logger.exception("generate_appeal_summary failed (kind=%s)", kind)
        return ""


async def build_appeal(
    kind: str,
    payload: dict,
    space: SpaceContext | None = None,
) -> tuple[str, list[float]]:
    """appeal_summary を生成し、その埋め込み appeal_vector も返す。

    取り込み時に各マスタへ付与するためのヘルパ。要約が空なら埋め込みも空。
    """
    summary = await generate_appeal_summary(kind, payload, space=space)
    if not summary:
        return "", []
    vector = await embed_text(summary, space=space)
    return summary, vector
