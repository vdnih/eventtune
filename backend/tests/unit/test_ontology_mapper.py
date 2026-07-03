"""OntologyMapper の解釈フェーズ（純粋関数）の回帰テスト（ADR-013）。

ADR-013 により CSV パスの行解釈は AI が直接担い、OntologyMapper は TXT パス（map_extraction）のみを担う。
"""

from agents.ontology_mapper import OntologyMapper, _normalize_name
from ontology import DocumentExtractionResult

_mapper = OntologyMapper()


def test_normalize_name_folds_width_space_case():
    assert _normalize_name("２０２５ 秋 展示会") == _normalize_name("2025秋展示会")
    assert _normalize_name("ACME Corp") == _normalize_name("acme　corp")
    assert _normalize_name("  ＡＢＣ ") == "abc"
    assert _normalize_name("") == ""


def test_map_extraction_event_has_no_id_only_name():
    """map_extraction が events を InterpretedRecord として返し、PK を含まないことを確認。"""
    extraction = DocumentExtractionResult(
        detected_entity_types=["Event"],
        events=[{"name": "2025秋展示会", "event_type": "展示会", "status": "終了"}],
    )
    result = _mapper.map_extraction(extraction, space_id="s1")
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.kind == "events"
    assert rec.name == "2025秋展示会"
    assert "event_id" not in rec.payload


def test_map_extraction_content_carries_event_link():
    """map_extraction の content_assets がイベントへのリンクを保持することを確認。"""
    extraction = DocumentExtractionResult(
        detected_entity_types=["Event", "ContentAsset"],
        events=[{"name": "2025秋展示会"}],
        content_assets=[{"name": "導入事例A", "content_type": "導入事例", "url": "http://x"}],
    )
    result = _mapper.map_extraction(extraction, space_id="s1")
    content_recs = [r for r in result.records if r.kind == "contents"]
    assert len(content_recs) == 1
    assert content_recs[0].links.get("event") == "2025秋展示会"
