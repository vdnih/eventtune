"""取り込みパイプライン（process_batch: Read→Interpret→Conform→Bind→Derive→Report）の
オフライン結合テスト（ADR-015）。

実 Firestore/AI を使わず、Fake な ScopedClient と stub した appeal 生成・文書抽出で、
承認済み BatchPlan の実行が狙いどおり収束することを固定する:
- 承認済み変換仕様（列対応・リンク列）がそのまま適用される（理解のやり直しなし）。
- イベントリンクは 行の列値 → 確認済み既定イベント → 保留（pending）。フォールバック・
  サイレントスキップは存在しない。
- 全観測ブロックの行き先が source_records に bound/pending/skipped + 理由で記録される。
"""

import asyncio
import io

import docx as docx_lib

import agents.data_integration_agent as agent
from agents.data_integration_agent import process_batch
from ontology import BatchPlan, DefaultEventPlan, FilePlan, TargetPlan
from tests.unit.fakes import FakeDB


def _make_docx(text: str) -> bytes:
    document = docx_lib.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _fake_appeal(monkeypatch):
    async def _fake_build_appeal(kind, payload, space=None):
        return f"summary:{kind}", [0.1, 0.2]

    monkeypatch.setattr(agent.semantic_search, "build_appeal", _fake_build_appeal)


def _run(db, files, plan):
    return asyncio.run(process_batch(files, "batch_test", db, plan, space=None))


_LEADS_CSV = (
    "メール,氏名,会社,サービス,イベント名\n"
    "t@a.com,田中太郎,ACME,プロダクトA,２０２５秋 展示会\n"
    "t@a.com,田中太郎,ACME,プロダクトA,２０２５秋 展示会\n"
).encode()

_LEADS_TARGET = TargetPlan(
    entity_type="event_attendances",
    column_map={
        "メール": "email",
        "氏名": "name",
        "会社": "company_name",
        "サービス": "product_link_names",
    },
    link_columns={"event": "イベント名"},
)


def test_pipeline_converges_links_and_dedups(monkeypatch):
    """表記揺れ・重複行・文書由来のマスタが単一の実在エンティティへ収束する。"""
    _fake_appeal(monkeypatch)

    async def _fake_doc_extractor(text, target_kinds, business_context, space=None):
        return {
            "events": [{"name": "2025秋展示会"}],
            "contents": [
                {
                    "content_name": "導入事例A",
                    "url": "http://x",
                    "event_link_name": "2025 秋 展示会",
                }
            ],
        }

    monkeypatch.setattr(agent, "run_document_extractor", _fake_doc_extractor)

    plan = BatchPlan(
        files=[
            FilePlan(filename="leads.csv", targets=[_LEADS_TARGET]),
            FilePlan(
                filename="overview.txt",
                targets=[
                    TargetPlan(entity_type="events"),
                    TargetPlan(entity_type="contents"),
                ],
            ),
        ]
    )
    db = FakeDB()
    result = _run(db, [("leads.csv", _LEADS_CSV), ("overview.txt", "概要".encode())], plan)

    # イベントは表記揺れに依らず 1 つへ収束
    events = db.docs("events")
    assert len(events) == 1
    event_id = events[0]["event_id"]
    assert events[0]["name"] == "2025秋展示会"

    # 参加ファクトが実在イベントへ JOIN 可能。同一 (person,event,action) は冪等
    atts = db.docs("event_attendances")
    assert len(atts) == 1
    assert atts[0]["event_id"] == event_id

    # contents→event リンクが解決
    contents = db.docs("contents")
    assert len(contents) == 1
    assert contents[0]["linked_event_id"] == event_id

    # 同一 email は 1 person に統合。導出ステージで appeal が付与される
    persons = db.docs("persons")
    assert len(persons) == 1
    assert persons[0]["appeal_summary"] == "summary:person"

    assert len(db.docs("product_interests")) == 1
    assert len(db.docs("products")) == 1

    # 全観測ブロックが bound として着地する（黙って捨てない）
    sources = db.docs("source_records")
    assert len(sources) == 3  # CSV 2行 + 文書 1件
    assert all(s["status"] == "bound" for s in sources)
    assert result.pending_count == 0


def test_docx_document_integrates_like_txt(monkeypatch):
    """実際の (モックしていない) Read ステージが .docx を解析し、.txt 同様に収束する。"""
    _fake_appeal(monkeypatch)

    async def _fake_doc_extractor(text, target_kinds, business_context, space=None):
        assert "概要" in text  # 実際の docx 抽出テキストが Interpret に渡っている
        return {"events": [{"name": "2025秋展示会"}]}

    monkeypatch.setattr(agent, "run_document_extractor", _fake_doc_extractor)

    plan = BatchPlan(
        files=[FilePlan(filename="overview.docx", targets=[TargetPlan(entity_type="events")])]
    )
    db = FakeDB()
    result = _run(db, [("overview.docx", _make_docx("概要"))], plan)

    events = db.docs("events")
    assert len(events) == 1
    assert events[0]["name"] == "2025秋展示会"

    sources = db.docs("source_records")
    assert len(sources) == 1
    assert sources[0]["status"] == "bound"
    assert result.pending_count == 0


def test_confirmed_default_event_binds_linkless_rows(monkeypatch):
    """イベント列の無い参加者CSVは、確認済み既定イベントへ束ねられる（旧フォールバックの置換）。"""
    _fake_appeal(monkeypatch)
    csv = "メール,氏名\na@a.com,田中太郎\nb@b.com,鈴木花子\n".encode()
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="スマート工場EXPO 2025秋", evidence="概要より"),
        files=[
            FilePlan(
                filename="leads.csv",
                targets=[
                    TargetPlan(
                        entity_type="event_attendances",
                        column_map={"メール": "email", "氏名": "name"},
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    result = _run(db, [("leads.csv", csv)], plan)

    events = db.docs("events")
    assert len(events) == 1  # 確認済み既定イベントは実在化される
    assert events[0]["name"] == "スマート工場EXPO 2025秋"
    atts = db.docs("event_attendances")
    assert len(atts) == 2
    assert all(a["event_id"] == events[0]["event_id"] for a in atts)
    assert result.pending_count == 0


def test_no_event_goes_pending_not_silent(monkeypatch):
    """イベントリンクが決まらない観測は保留（pending）になり、黙って消えない。person は作る。"""
    _fake_appeal(monkeypatch)
    csv = "メール,氏名\na@a.com,田中太郎\n".encode()
    plan = BatchPlan(
        default_event=None,  # ユーザーが「イベントなし」を選択
        files=[
            FilePlan(
                filename="leads.csv",
                targets=[
                    TargetPlan(
                        entity_type="event_attendances",
                        column_map={"メール": "email", "氏名": "name"},
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    result = _run(db, [("leads.csv", csv)], plan)

    assert len(db.docs("persons")) == 1  # person 自体は作る（現行踏襲）
    assert len(db.docs("event_attendances")) == 0
    sources = db.docs("source_records")
    assert len(sources) == 1
    assert sources[0]["status"] == "pending"
    assert "イベントリンク未解決" in sources[0]["reason"]
    assert result.pending_count == 1
    # バッチ doc にも保留が集計される
    batch = db.store["integration_jobs/batch_test"]
    assert batch["pending_count"] == 1
    # AI 不通時は日本語サマリにフォールバックする（事実は失わない）
    assert "保留" in batch["report_markdown"]
    assert "```json" not in batch["report_markdown"]


def test_row_link_column_overrides_default_event(monkeypatch):
    """行の列値は確認済み既定イベントより常に優先される。"""
    _fake_appeal(monkeypatch)
    csv = (
        "メール,氏名,イベント名\n"
        "a@a.com,田中太郎,イベントA\n"
        "b@b.com,鈴木花子,イベントB\n"
        "c@c.com,佐藤次郎,\n"
    ).encode()
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="既定イベントC"),
        files=[
            FilePlan(
                filename="leads.csv",
                targets=[
                    TargetPlan(
                        entity_type="event_attendances",
                        column_map={"メール": "email", "氏名": "name"},
                        link_columns={"event": "イベント名"},
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    _run(db, [("leads.csv", csv)], plan)

    events = {e["name"]: e["event_id"] for e in db.docs("events")}
    assert set(events) == {"イベントA", "イベントB", "既定イベントC"}
    atts = db.docs("event_attendances")
    assert len(atts) == 3
    bound_events = sorted(a["event_id"] for a in atts)
    assert bound_events.count(events["イベントA"]) == 1
    assert bound_events.count(events["イベントB"]) == 1
    assert bound_events.count(events["既定イベントC"]) == 1  # 列が空の行のみ既定へ


def test_cost_items_retain_resolved_event_id(monkeypatch):
    """費用ファクトは解決済み event_id を保持する（必須フィールド既定の空文字で潰さない）。

    回帰: _fill_required_fields が必須 str の event_id を "" で埋め、fact 構築時に
    payload を後置きしていたため、解決済み ev_id を空文字が上書きしていた。結果
    cost_items.event_id が常に "" となり、events との JOIN・表示名解決が壊れていた。
    """
    _fake_appeal(monkeypatch)
    csv = "費目,内容,金額\n会場費・出展費,ブース出展料,2200000\n".encode()
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="スマート工場EXPO 2025秋", evidence="概要より"),
        files=[
            FilePlan(
                filename="costs.csv",
                targets=[
                    TargetPlan(
                        entity_type="cost_items",
                        column_map={
                            "費目": "category",
                            "内容": "description",
                            "金額": "amount_jpy",
                        },
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    result = _run(db, [("costs.csv", csv)], plan)

    events = db.docs("events")
    assert len(events) == 1
    event_id = events[0]["event_id"]

    costs = db.docs("cost_items")
    assert len(costs) == 1
    assert costs[0]["event_id"] == event_id
    assert costs[0]["event_id"]  # 空文字でない（回帰ガード）
    assert result.pending_count == 0


def test_document_patches_fold_into_event(monkeypatch):
    """文書由来の KPI/アンケートが当該イベントの doc へ畳み込まれる。"""
    _fake_appeal(monkeypatch)

    async def _fake_doc_extractor(text, target_kinds, business_context, space=None):
        return {
            "events": [{"name": "2025秋展示会", "venue": "東京ビッグサイト"}],
            "event_kpi": [{"total_visitors_to_booth": 1200, "pipeline_value_jpy": 5000000}],
            "survey_summary": [{"nps_score": 42.0, "total_survey_responses": 88}],
        }

    monkeypatch.setattr(agent, "run_document_extractor", _fake_doc_extractor)
    plan = BatchPlan(
        files=[
            FilePlan(
                filename="overview.txt",
                targets=[
                    TargetPlan(entity_type="events"),
                    TargetPlan(entity_type="event_kpi"),
                    TargetPlan(entity_type="survey_summary"),
                ],
            )
        ]
    )
    db = FakeDB()
    _run(db, [("overview.txt", "概要".encode())], plan)

    events = db.docs("events")
    assert len(events) == 1
    ev = events[0]
    assert ev["venue"] == "東京ビッグサイト"
    assert ev["total_visitors_to_booth"] == 1200
    assert ev["nps_score"] == 42.0


def test_skipped_rows_are_recorded_with_reason(monkeypatch):
    """最小要件を満たさない行は skipped + 理由で着地する。"""
    _fake_appeal(monkeypatch)
    csv = "メール,氏名\na@a.com,\n".encode()  # 氏名が空
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="イベントX"),
        files=[
            FilePlan(
                filename="leads.csv",
                targets=[
                    TargetPlan(
                        entity_type="event_attendances",
                        column_map={"メール": "email", "氏名": "name"},
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    result = _run(db, [("leads.csv", csv)], plan)

    sources = db.docs("source_records")
    assert len(sources) == 1
    assert sources[0]["status"] == "skipped"
    assert "氏名" in sources[0]["reason"]
    assert result.skipped_count == 1
    assert len(db.docs("persons")) == 0


def test_heartbeat_progresses_to_report(monkeypatch):
    """ステージのハートビートが刻まれ、完了時は report で終わる。"""
    _fake_appeal(monkeypatch)
    csv = "メール,氏名\na@a.com,田中\n".encode()
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="イベントX"),
        files=[
            FilePlan(
                filename="leads.csv",
                targets=[
                    TargetPlan(
                        entity_type="event_attendances",
                        column_map={"メール": "email", "氏名": "name"},
                    )
                ],
            )
        ],
    )
    db = FakeDB()
    _run(db, [("leads.csv", csv)], plan)
    batch = db.store["integration_jobs/batch_test"]
    assert batch["stage"] == "report"
    assert batch["heartbeat_at"]
    assert batch["plan"]["default_event"]["name"] == "イベントX"  # 実行されたプランが記録される


def test_fallback_report_is_human_readable_not_json():
    """AI整形が失敗したときのフォールバックは、生JSONではなく日本語サマリになる。"""
    aggregate = {
        "created": {"events": 1, "persons": 0},
        "pending_count": 0,
        "pending": [],
        "skipped_count": 1,
        "skipped": [{"entity_type": "memo.txt", "reason": "文書抽出に失敗"}],
        "new_masters": [],
        "fuzzy_matches": [],
        "default_event": None,
    }
    text = agent._fallback_report(aggregate)
    assert "{" not in text
    assert "```json" not in text
    assert "イベント 1件" in text
    assert "スキップ: 1件" in text
