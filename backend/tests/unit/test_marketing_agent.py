"""define_segment の buckets 正規化（_normalize_buckets）の回帰テスト、および
個別方式（generate_individual_deliverables / _individual_run）のテスト。

背景: LLM は buckets を JSON配列**文字列**で渡してくることがあり、素朴な list(...) が
文字列を1文字ずつに分解していた（→ generate_patterns が大量に細分化）。
本テストはその修正を固定する。
"""

import json

import agents.marketing_agent as marketing_agent
from agents.marketing_agent import (
    _normalize_buckets,
    _pattern_doc_id,
    _PatternBlock,
    _PatternSchema,
    make_tools,
)
from tests.unit.fakes import FakeDB


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


# ── _pattern_doc_id（Firestoreパス分割エラーの回帰テスト）────────────────────────
#
# 背景: bucket に '/' が含まれると Firestore が pattern_id をパス区切りとして
# 再解釈し、generate_patterns の db.collection(...).document(pattern_id) が
# "A document must have an even number of path elements" で失敗していた。


def test_pattern_doc_id_replaces_slash_in_bucket():
    doc_id = _pattern_doc_id("既存/新規", "EMAIL")
    assert "/" not in doc_id
    assert doc_id == "既存／新規__EMAIL"


def test_pattern_doc_id_matches_legacy_format_without_slash():
    assert _pattern_doc_id("資格法令対応×高熱量", "EMAIL") == "資格法令対応×高熱量__EMAIL"


# ── 個別方式（generate_individual_deliverables / _individual_run）───────────────


def test_individual_run_writes_deliverables_and_marks_run_done(monkeypatch):
    db = FakeDB()
    db.collection("persons").document("p1").set(
        {"person_id": "p1", "name": "山田太郎", "space_id": "sp1"}
    )
    db.collection("persons").document("p2").set(
        {"person_id": "p2", "name": "佐藤花子", "space_id": "sp1"}
    )

    def fake_generate_one_individual(
        space, person, account_name, relevant_contents, purpose, context, output_format="EMAIL"
    ):
        return _PatternSchema(
            subject=f"{person['name']}様への件名",
            blocks=[
                _PatternBlock(
                    block_type="body",
                    reason_for_inclusion="テスト用の固定理由",
                    block_text="こんにちは。",
                )
            ],
        )

    monkeypatch.setattr(marketing_agent, "_generate_one_individual", fake_generate_one_individual)

    done = marketing_agent._individual_run(
        db,
        space=None,
        person_ids=["p1", "p2"],
        purpose="テスト施策",
        context="",
        output_format="EMAIL",
        run_id="run_test0001",
    )

    assert done == 2

    run = db.collection("marketing_runs").document("run_test0001").get().to_dict()
    assert run["status"] == "done"
    assert run["total"] == 2
    assert run["done"] == 2
    assert run["segment_id"] == ""
    assert run["snapshot_id"] == ""

    deliverables = db.docs("marketing_runs/run_test0001/deliverables")
    assert len(deliverables) == 2
    assert {d["person_id"] for d in deliverables} == {"p1", "p2"}
    for d in deliverables:
        assert d["bucket"] == "個別"
        assert d["format"] == "EMAIL"
        assert d["pattern_id"] is None
        assert d["subject"].endswith("様への件名")
        assert d["blocks"][0]["reason_for_inclusion"] == "テスト用の固定理由"


def test_individual_run_skips_missing_person(monkeypatch):
    db = FakeDB()
    db.collection("persons").document("p1").set(
        {"person_id": "p1", "name": "存在する人", "space_id": "sp1"}
    )
    # p2 は persons に存在しない

    monkeypatch.setattr(
        marketing_agent,
        "_generate_one_individual",
        lambda *a, **k: _PatternSchema(subject="件名", blocks=[]),
    )

    done = marketing_agent._individual_run(
        db,
        space=None,
        person_ids=["p1", "p2"],
        purpose="テスト施策",
        context="",
        output_format="EMAIL",
        run_id="run_test0002",
    )

    assert done == 1
    deliverables = db.docs("marketing_runs/run_test0002/deliverables")
    assert len(deliverables) == 1
    assert deliverables[0]["person_id"] == "p1"


def test_generate_individual_deliverables_rejects_too_many_persons():
    db = FakeDB()
    tools = make_tools(db, space=None)
    tool = next(t for t in tools if t.__name__ == "generate_individual_deliverables")

    result = json.loads(tool(person_ids=[f"p{i}" for i in range(31)], purpose="テスト施策"))

    assert "error" in result
    assert "セグメント方式" in result["error"]


def test_generate_individual_deliverables_rejects_empty_persons():
    db = FakeDB()
    tools = make_tools(db, space=None)
    tool = next(t for t in tools if t.__name__ == "generate_individual_deliverables")

    result = json.loads(tool(person_ids=[], purpose="テスト施策"))

    assert "error" in result
