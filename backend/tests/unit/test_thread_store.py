"""thread_store の run_id / パターン抽出ロジック（純粋関数）のユニットテスト。

Firestore に触れない範囲（tool_result イベントのパースだけ）を対象とする。
"""

import json

from thread_store import extract_pattern_segment_from_result, extract_run_id_from_result


def _tool_result(tool_name: str, result: object) -> dict:
    return {"type": "tool_result", "tool_name": tool_name, "result": result}


def test_extract_run_id_from_run_assembly_result():
    event = _tool_result("run_assembly", {"result": json.dumps({"run_id": "run_abc123456789"})})
    assert extract_run_id_from_result(event) == ("run_abc123456789", "run_assembly")


def test_extract_run_id_from_individual_deliverables_result():
    # generate_individual_deliverables も run_assembly と同じ形式で run_id を返す
    # （個別方式はそれ自体が確定処理のため、成果物一覧のカード表示を全件出し分けるのに使う）。
    event = _tool_result(
        "generate_individual_deliverables",
        {"result": json.dumps({"run_id": "run_def123456789", "count": 5, "format": "EMAIL"})},
    )
    assert extract_run_id_from_result(event) == (
        "run_def123456789",
        "generate_individual_deliverables",
    )


def test_extract_run_id_ignores_unrelated_tool():
    event = _tool_result("define_segment", {"result": json.dumps({"run_id": "run_should_ignore0"})})
    assert extract_run_id_from_result(event) is None


def test_extract_run_id_handles_dict_result_without_json_string():
    # result がすでに dict の場合（文字列 JSON でないケース）も拾えること
    event = _tool_result("run_assembly", {"result": {"run_id": "run_dictresult0000"}})
    assert extract_run_id_from_result(event) == ("run_dictresult0000", "run_assembly")


def test_extract_run_id_handles_malformed_json():
    event = _tool_result("run_assembly", {"result": "not json"})
    assert extract_run_id_from_result(event) is None


def test_extract_pattern_segment_from_generate_patterns_result():
    event = _tool_result(
        "generate_patterns",
        {
            "result": json.dumps(
                {
                    "segment_id": "seg_123",
                    "format": "EMAIL",
                    "patterns": [{"bucket": "A", "pattern_id": "A__EMAIL", "subject": "件名"}],
                    "count": 1,
                }
            )
        },
    )
    assert extract_pattern_segment_from_result(event) == ("seg_123", "EMAIL")


def test_extract_pattern_segment_ignores_unrelated_tool():
    event = _tool_result(
        "run_assembly", {"result": json.dumps({"segment_id": "seg_123", "format": "EMAIL"})}
    )
    assert extract_pattern_segment_from_result(event) is None
