"""OntologyMapper の解釈フェーズ（純粋関数）の回帰テスト（ADR-011）。

背景: 取り込みを依存順の多段へ再設計し、mapper は最終エンティティではなく中間レコード
（PersonObservation / InterpretedRecord）を返すようにした。安定ID（名前ハッシュ）は廃止し、
照合は _normalize_name で行う。接客事実は Person ではなく観測（→ EventAttendance）へ載る。
"""

from ontology import ColumnMappingResult, EngagementLevel
from agents.ontology_mapper import OntologyMapper, _normalize_name

_mapper = OntologyMapper()


def test_normalize_name_folds_width_space_case():
    # 全角/半角・空白・大小の揺れを同一キーへ畳む
    assert _normalize_name("２０２５ 秋 展示会") == _normalize_name("2025秋展示会")
    assert _normalize_name("ACME Corp") == _normalize_name("acme　corp")  # 全角スペース
    assert _normalize_name("  ＡＢＣ ") == "abc"
    assert _normalize_name("") == ""


def _person_mapping() -> ColumnMappingResult:
    return ColumnMappingResult(
        entity_type="persons",
        column_map={
            "氏名": "name",
            "会社": "company_name",
            "役職": "job_title",
            "メール": "email",
            "課題": "__challenge",
            "担当": "__event_owner",
            "所感": "__memo",
        },
        default_links={"event": "2025秋展示会"},
    )


def test_decompose_person_puts_encounter_facts_on_observation():
    rows = [{
        "氏名": "田中太郎", "会社": "ACME", "役職": "部長",
        "メール": "tanaka@acme.co.jp",
        "課題": "人手不足", "担当": "佐藤", "所感": "デモ希望",
    }]
    result = _mapper.map_rows(_person_mapping(), rows, space_id="s1")

    assert len(result.person_observations) == 1
    obs = result.person_observations[0]
    # 接客事実は観測（→ EventAttendance）に載る
    assert obs.owner_staff == "佐藤"
    assert obs.challenge_note == "人手不足"
    assert obs.memo == "デモ希望"
    # リンクは“名”のまま（PK は採番しない）
    assert obs.event_link_name == "2025秋展示会"
    assert obs.company_name == "ACME"
    assert obs.email == "tanaka@acme.co.jp"
    # Person には notes フィールドを作らない（中間表現に notes は存在しない）
    assert not hasattr(obs, "notes")


def test_decompose_person_skips_blank_name():
    rows = [{"氏名": "", "会社": "ACME"}]
    result = _mapper.map_rows(_person_mapping(), rows, space_id="s1")
    assert result.person_observations == []
    assert len(result.skipped) == 1


def test_decompose_person_classifies_engagement_from_memo():
    rows = [{"氏名": "山田", "所感": "アポ獲得済み、次回提案予定"}]
    mapping = ColumnMappingResult(
        entity_type="persons",
        column_map={"氏名": "name", "所感": "__memo"},
    )
    result = _mapper.map_rows(mapping, rows, space_id="s1")
    obs = result.person_observations[0]
    assert obs.engagement_level == EngagementLevel.APPOINTMENT_BOOKED


def test_content_carries_event_link_name():
    # contents→event リンクが空にならない（従来の穴の修正）
    mapping = ColumnMappingResult(
        entity_type="contents",
        column_map={"素材名": "content_name", "種別": "content_type", "URL": "url"},
        default_links={"event": "2025秋展示会"},
    )
    rows = [{"素材名": "導入事例A", "種別": "導入事例", "URL": "http://x"}]
    result = _mapper.map_rows(mapping, rows, space_id="s1")
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.kind == "contents"
    assert rec.links.get("event") == "2025秋展示会"


def test_event_record_has_no_id_only_name():
    mapping = ColumnMappingResult(
        entity_type="events",
        column_map={"名称": "name", "種別": "event_type"},
    )
    rows = [{"名称": "2025秋展示会", "種別": "展示会"}]
    result = _mapper.map_rows(mapping, rows, space_id="s1")
    rec = result.records[0]
    assert rec.kind == "events"
    assert rec.name == "2025秋展示会"
    # payload には PK を含まない（conform が UUID を採番する）
    assert "event_id" not in rec.payload
