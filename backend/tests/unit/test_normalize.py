"""normalize（照合キー正規化・登録制 normalizer）のユニットテスト。"""

from ingestion.normalize import (
    _normalize_name,
    _split_names,
    int_with_unit,
    iso_date,
    money_jpy,
    percent_rate,
)


class TestNormalizeName:
    def test_folds_width_space_case(self):
        assert _normalize_name("ＡＢＣ 株式会社") == _normalize_name("ABC株式会社")
        assert _normalize_name("Acme Inc") == _normalize_name("acme  inc")

    def test_empty(self):
        assert _normalize_name("") == ""


class TestSplitNames:
    def test_splits_japanese_separators(self):
        assert _split_names("製品A、製品B") == ["製品A", "製品B"]
        assert _split_names("A/B;C") == ["A", "B", "C"]

    def test_drops_long_cells_and_dupes(self):
        assert _split_names("A、A") == ["A"]
        assert _split_names("あ" * 61) == []


class TestMoneyJpy:
    def test_strips_comma_and_yen(self):
        value, reason = money_jpy("1,200,000円")
        assert value == 1200000.0
        assert reason is not None

    def test_passthrough_number(self):
        assert money_jpy(500.0) == (500.0, None)

    def test_unparseable_is_zero_with_reason(self):
        value, reason = money_jpy("未定")
        assert value == 0.0
        assert "未定" in reason


class TestIsoDate:
    def test_slash_format_with_time(self):
        value, reason = iso_date("2026/06/07 10:05")
        assert value == "2026-06-07"
        assert reason is not None

    def test_kanji_format(self):
        assert iso_date("2026年6月7日")[0] == "2026-06-07"

    def test_already_iso(self):
        assert iso_date("2026-06-07") == ("2026-06-07", None)

    def test_unparseable_passthrough(self):
        assert iso_date("来週")[0] == "来週"


class TestIntWithUnit:
    def test_strips_unit(self):
        value, reason = int_with_unit("150名")
        assert value == 150
        assert reason is not None

    def test_comma(self):
        assert int_with_unit("1,200")[0] == 1200

    def test_no_number(self):
        value, reason = int_with_unit("多数")
        assert value == 0
        assert reason is not None


class TestPercentRate:
    def test_percent_string(self):
        value, reason = percent_rate("61%")
        assert value == 0.61
        assert reason is not None

    def test_already_fraction(self):
        assert percent_rate("0.61") == (0.61, None)

    def test_over_one_treated_as_percent(self):
        assert percent_rate("61")[0] == 0.61
