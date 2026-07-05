"""
normalize — 照合キーの正規化と、値の normalizer（登録制の純関数）

normalizer の契約: `Normalizer = Callable[[Any], tuple[Any, str | None]]`
戻り値は (変換後の値, 変換の説明 or None)。説明が非 None のとき、呼び出し側は
TransformDecision として監査記録に残す（INGESTION_MAPPING §4 解釈エンジン）。
"""

import re
import unicodedata
from collections.abc import Callable
from typing import Any

Normalizer = Callable[[Any], tuple[Any, str | None]]


def _normalize_name(s: str) -> str:
    """照合キー用の正規化: NFKC（全角半角統一）→ 全空白除去 → lower。

    表記揺れ（全角/半角・空白・大小）を吸収して同一マスタへ畳むための比較キー。
    ID 生成には使わない（PK は UUID）。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _split_names(text: str) -> list[str]:
    """区切り文字で複数の名称に分割する（製品名セルの「A、B」等）。"""
    if not text:
        return []
    parts = re.split(r"[、,/／;；]", text)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        # 長すぎるセルはメモ等の可能性が高く、製品名として扱わない
        if s and len(s) <= 60 and s not in out:
            out.append(s)
    return out


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ── 登録制 normalizer ─────────────────────────────────────────────────────────


def money_jpy(value: Any) -> tuple[float, str | None]:
    """金額文字列を数値化する（「1,200,000円」→ 1200000.0）。変換不能は 0.0。"""
    if value is None or value == "":
        return 0.0, None
    if isinstance(value, int | float):
        return float(value), None
    raw = str(value)
    cleaned = unicodedata.normalize("NFKC", raw).replace(",", "").replace("円", "")
    cleaned = cleaned.replace("¥", "").replace("\\", "").strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return 0.0, f"数値化できない値 '{raw}' → 0"
    if cleaned != raw:
        return amount, f"'{raw}' からカンマ・通貨記号を除去して数値化"
    return amount, None


_DATE_PATTERNS = (
    re.compile(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})"),  # 2026/06/07, 2026-06-07, 2026年6月7日
)


def iso_date(value: Any) -> tuple[str, str | None]:
    """日付文字列を YYYY-MM-DD に揃える。解釈できない値はそのまま返す。"""
    if value is None or value == "":
        return "", None
    raw = unicodedata.normalize("NFKC", str(value)).strip()
    for pat in _DATE_PATTERNS:
        m = pat.search(raw)
        if m:
            y, mo, d = m.groups()
            normalized = f"{y}-{int(mo):02d}-{int(d):02d}"
            reason = None if normalized == raw else f"'{raw}' を ISO 日付 '{normalized}' に正規化"
            return normalized, reason
    return raw, None


_INT_PATTERN = re.compile(r"-?\d+")


def int_with_unit(value: Any) -> tuple[int, str | None]:
    """単位付き数値を整数化する（「150名」→ 150）。数値が無ければ 0。"""
    if value is None or value == "":
        return 0, None
    if isinstance(value, int | float):
        return int(value), None
    raw = str(value)
    cleaned = unicodedata.normalize("NFKC", raw).replace(",", "")
    m = _INT_PATTERN.search(cleaned)
    if m is None:
        return 0, f"数値が見つからない値 '{raw}' → 0"
    n = int(m.group())
    if m.group() != raw.strip():
        return n, f"'{raw}' から単位等を除去して数値化"
    return n, None


def percent_rate(value: Any) -> tuple[float, str | None]:
    """率を 0.0〜1.0 の小数に揃える（「61%」→ 0.61、61 → 0.61、0.61 → 0.61）。"""
    if value is None or value == "":
        return 0.0, None
    raw = str(value)
    cleaned = unicodedata.normalize("NFKC", raw).replace("%", "").replace("％", "").strip()
    try:
        n = float(cleaned)
    except ValueError:
        return 0.0, f"率として解釈できない値 '{raw}' → 0"
    if "%" in unicodedata.normalize("NFKC", raw) or n > 1.0:
        return n / 100.0, f"'{raw}' を割合の小数 {n / 100.0} に変換"
    return n, None
