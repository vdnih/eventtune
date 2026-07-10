"""IngestionSpec レジストリの整合テスト（ドリフトをテスト失敗に変える）。"""

import pytest
from pydantic import BaseModel

from ingestion.engine import _enum_default, enum_fields_of
from ingestion.prompts import render_ontology_definition
from ingestion.specs import (
    REGISTRY,
    IngestionSpec,
    _check_registry,
    file_target_kinds,
)
from ontology import ContentType, CostCategory, EventType


class TestRegistryConsistency:
    def test_registry_is_consistent(self):
        # import 時にも実行されるが、テストとしても明示的に検証する
        _check_registry()

    def test_masters_have_natural_key(self):
        for spec in REGISTRY.values():
            if spec.role == "master":
                assert spec.natural_key, spec.kind
                assert spec.collection

    def test_file_target_kinds_excludes_derived(self):
        kinds = file_target_kinds()
        assert "persons" not in kinds  # 観測から名寄せで導出される
        assert "product_interests" not in kinds  # 接客観測から導出される
        assert "event_attendances" in kinds
        assert "cost_items" in kinds

    def test_required_links_have_batch_default_for_event(self):
        for spec in REGISTRY.values():
            for ls in spec.links.values():
                if ls.required and ls.target == "events":
                    assert ls.default_from_batch, spec.kind

    def test_inconsistent_observation_is_rejected(self):
        class BogusObservation(BaseModel):
            no_such_field: str | None = None

        from ontology import Product

        bad = {
            "products": IngestionSpec(
                kind="products",
                role="master",
                model=Product,
                collection="products",
                id_field="product_id",
                id_prefix="product_",
                natural_key=("product_name",),
                observation=BogusObservation,
            )
        }
        with pytest.raises(RuntimeError, match="no_such_field"):
            _check_registry(bad)


class TestEnumHandling:
    def test_every_observed_enum_field_has_default(self):
        for spec in REGISTRY.values():
            if spec.observation is None:
                continue
            for fld, enum_type in enum_fields_of(spec.model).items():
                if fld in spec.observation.model_fields:
                    assert _enum_default(spec, fld, enum_type) in enum_type

    def test_known_defaults(self):
        assert _enum_default(REGISTRY["cost_items"], "category", CostCategory) == CostCategory.OTHER
        assert (
            _enum_default(REGISTRY["contents"], "content_type", ContentType)
            == ContentType.WHITE_PAPER
        )
        assert _enum_default(REGISTRY["events"], "event_type", EventType) == EventType.TRADE_SHOW


class TestPromptRenderer:
    def test_mentions_every_kind(self):
        rendered = render_ontology_definition()
        for kind in REGISTRY:
            assert kind in rendered

    def test_mentions_enum_vocabulary(self):
        rendered = render_ontology_definition()
        assert "展示会" in rendered  # EventType
        assert "会場費・出展費" in rendered  # CostCategory
        assert "導入事例" in rendered  # ContentType

    def test_mentions_link_semantics(self):
        rendered = render_ontology_definition()
        assert "バッチ既定イベントで補完可" in rendered
        assert "challenge_note" in rendered
