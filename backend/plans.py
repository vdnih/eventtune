"""
プラン定義とクレジット換算

課金は「クレジット」という単一概念で扱う。生実績（メータリングで貯めたトークン・実行時間）に
換算レートを掛けてクレジットを算出し、プランは月次クレジット上限として定義する。
トークン上限・時間上限を別々には持たない。

設計上の分離（重要）:
- 生実績の計測は metering.py（種別ごとの素の値だけを貯める）。
- クレジットへの換算はここ（読み取り時に算出。保存しない → レート改定が遡及的・一貫して反映）。

今回は計測とクレジット算出のみ。利用上限の enforcement（超過拒否）はまだ行わない
（check_quota はスタブ）。将来 plan に応じて check_quota を有効化する拡張点とする。
"""

from __future__ import annotations

from ontology import Plan

# ── クレジット換算レート ───────────────────────────────────────────────────────
# 1トークン / 1ms あたりのクレジット。実コスト（モデル単価・コンピュート単価）に比例させる。
# モデル/リソース種別ごとに設定。未知のキーは _DEFAULT_* にフォールバックする。

# 入力/出力トークン → クレジット（モデル別）
TOKEN_CREDIT_RATES: dict[str, dict[str, float]] = {
    "gemini-3.1-flash-lite": {"input": 0.000001, "output": 0.000004},
}
_DEFAULT_TOKEN_RATE = {"input": 0.000002, "output": 0.000008}

# 実行時間(ms) → クレジット（リソース種別別）
TIME_CREDIT_RATES: dict[str, float] = {
    "cloudrun-default": 0.0000005,
}
_DEFAULT_TIME_RATE = 0.0000005


# ── プラン上限（月次クレジット） ───────────────────────────────────────────────
PLAN_MONTHLY_CREDIT_LIMIT: dict[Plan, float] = {
    Plan.FREE:    1_000.0,
    Plan.PRO:     50_000.0,
    Plan.PREMIUM: 500_000.0,
}


def compute_credits(usage_doc: dict | None) -> float:
    """usage ドキュメント（生実績）からクレジット消費を算出する。

    usage_doc 形:
      { "llm": {model: {"input_tokens", "output_tokens"}}, "compute": {type: {"ms"}} }
    """
    if not usage_doc:
        return 0.0

    credits = 0.0

    for model, toks in (usage_doc.get("llm") or {}).items():
        rate = TOKEN_CREDIT_RATES.get(model, _DEFAULT_TOKEN_RATE)
        credits += (toks.get("input_tokens", 0) or 0) * rate["input"]
        credits += (toks.get("output_tokens", 0) or 0) * rate["output"]

    for rtype, comp in (usage_doc.get("compute") or {}).items():
        rate = TIME_CREDIT_RATES.get(rtype, _DEFAULT_TIME_RATE)
        credits += (comp.get("ms", 0) or 0) * rate

    return round(credits, 4)


def monthly_credit_limit(plan: Plan | str) -> float:
    if isinstance(plan, str):
        plan = Plan(plan)
    return PLAN_MONTHLY_CREDIT_LIMIT.get(plan, PLAN_MONTHLY_CREDIT_LIMIT[Plan.FREE])


def check_quota(plan: Plan | str, usage_doc: dict | None) -> bool:
    """当月クレジット消費がプラン上限内かを返す。

    NOTE: 現状は計測フェーズのため常に True を返すスタブ。将来 enforcement を
    有効化する際は本体（消費 < 上限）を返すよう切り替える。
    """
    # return compute_credits(usage_doc) < monthly_credit_limit(plan)
    return True
