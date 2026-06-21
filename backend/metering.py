"""
メータリング — スペース別リソース消費の計測

目的は将来の課金。原価に直結する **リソース消費の生実績** のみを記録する。
課金概念（クレジット）は生実績からの換算で導出するため（plans.compute_credits）、
ここでは換算せず生の値だけを貯める。機能単位メトリクス（メール生成数等）は取らない。

計測する2種:
- LLM利用:     モデル種別ごとの入出力トークン
- コンピュート: リソース種別ごとの実行時間(ms)

集計先: spaces/{space_id}/usage/{YYYY-MM}
  llm:     { "<model>":         { input_tokens, output_tokens } }
  compute: { "<resource_type>": { ms } }

集計はネスト dict + firestore.Increment を set(merge=True) で行う。モデル名はドットを
含む（例 gemini-3.1-flash-lite）ため、ドット区切りのフィールドパス文字列ではなく
dict のキーとして渡すことで誤分割を避ける。set(merge=True) は未作成ドキュメントも作成する。
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore

from space import SpaceContext

logger = logging.getLogger(__name__)

DEFAULT_COMPUTE_RESOURCE = "cloudrun-default"


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _usage_ref(space: SpaceContext):
    return space.col("usage").document(_period())


def _apply(space: SpaceContext, payload: dict) -> None:
    """ネスト dict（葉が firestore.Increment）を set(merge=True) で加算適用する。"""
    payload["period"] = _period()  # 初回作成時の識別用（既存は無害な上書き）
    try:
        _usage_ref(space).set(payload, merge=True)
    except Exception:
        # メータリングは本処理を阻害しない（計測失敗で機能を落とさない）
        logger.exception("metering failed: space=%s payload=%s", space.space_id, payload)


def record_llm(
    space: SpaceContext,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """LLMコールのトークン消費を記録する。"""
    in_tok, out_tok = int(input_tokens or 0), int(output_tokens or 0)
    if not (in_tok or out_tok):
        return
    _apply(space, {"llm": {model: {
        "input_tokens": firestore.Increment(in_tok),
        "output_tokens": firestore.Increment(out_tok),
    }}})


def record_compute(
    space: SpaceContext,
    ms: int,
    resource_type: str = DEFAULT_COMPUTE_RESOURCE,
) -> None:
    """コンピュートリソースの実行時間(ms)を記録する。"""
    ms = int(ms or 0)
    if not ms:
        return
    _apply(space, {"compute": {resource_type: {"ms": firestore.Increment(ms)}}})


def record_llm_response(space: SpaceContext, model: str, response: Any) -> None:
    """google-genai のレスポンスから usage_metadata を回収して記録する。

    現状 usage_metadata は破棄されているため、ここで input/output トークンを拾う。
    フィールド名は SDK 差異に備えて防御的に取得する。
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return
    in_tok = getattr(usage, "prompt_token_count", None)
    out_tok = getattr(usage, "candidates_token_count", None)
    record_llm(space, model, in_tok or 0, out_tok or 0)


@contextmanager
def metered(space: SpaceContext, resource_type: str = DEFAULT_COMPUTE_RESOURCE):
    """ブロックの実行時間を計測して compute に加算するコンテキストマネージャ。

    使用例:
        with metered(space):
            ... AIオペレーションやバックグラウンドジョブ ...
    """
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        record_compute(space, elapsed_ms, resource_type)
