"""define_segment の buckets 正規化（_normalize_buckets）の回帰テスト。

背景: LLM は buckets を JSON配列**文字列**で渡してくることがあり、素朴な list(...) が
文字列を1文字ずつに分解していた（→ generate_patterns が大量に細分化）。
本テストはその修正を固定する。
"""

from agents.marketing_agent import _normalize_buckets


def test_normalizes_plain_list():
    assert _normalize_buckets(["A×1", "B×2"]) == ["A×1", "B×2"]


def test_parses_json_array_string():
    # LLM が axes_json と同様に文字列で渡してくるケース
    raw = '["資格・法令対応×プロダクトA", "要員配置・自動化×プロダクトB", "技能伝承・多能工×両方"]'
    assert _normalize_buckets(raw) == [
        "資格・法令対応×プロダクトA",
        "要員配置・自動化×プロダクトB",
        "技能伝承・多能工×両方",
    ]


def test_rejects_degenerate_single_char_buckets():
    # 文字列を list() で分解してしまった痕跡（1文字バケットの羅列）はエラー
    degenerate = list('["資格×A"]')
    result = _normalize_buckets(degenerate)
    assert isinstance(result, dict) and "error" in result


def test_rejects_invalid_json_string():
    assert isinstance(_normalize_buckets("not json"), dict)


def test_rejects_empty():
    assert isinstance(_normalize_buckets([]), dict)
    assert isinstance(_normalize_buckets(["", "  "]), dict)


def test_strips_whitespace():
    assert _normalize_buckets(["  A×1  ", "B×2"]) == ["A×1", "B×2"]
