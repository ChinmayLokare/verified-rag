from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.conftest import REPO
from verified_rag.packs.interface import ConceptSpec, PackSpec, UnitSpec
from verified_rag.packs.loader import PackError, load_pack, load_spec


def test_finance_pack_loads(finance_spec: PackSpec) -> None:
    assert finance_spec.id == "finance"
    assert len(finance_spec.entities) == 10
    names = {c.name for c in finance_spec.concepts}
    assert {"revenue", "operating_income", "net_income", "total_assets"} <= names
    pack = load_pack("finance", REPO / "packs")
    assert callable(pack.create_connector)


def test_every_concept_alias_is_lowercase_text(finance_spec: PackSpec) -> None:
    for concept in finance_spec.concepts:
        assert concept.tags, concept.name
        for alias in concept.aliases:
            assert alias == alias.strip(), alias


def _spec(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "demo",
        "display_name": "Demo",
        "parser_version": "1",
        "document_types": ["report"],
        "entities": [{"ref": "X"}],
        "concepts": [
            ConceptSpec(name="a", description="", unit_kind="money", tags=("t:A",)),
        ],
        "units": {"USD": UnitSpec(kind="money")},
    }
    base.update(overrides)
    return base


def test_tag_mapped_to_two_concepts_is_rejected() -> None:
    concepts = [
        ConceptSpec(name="a", description="", unit_kind="money", tags=("t:X",)),
        ConceptSpec(name="b", description="", unit_kind="money", tags=("t:X",)),
    ]
    with pytest.raises(ValidationError, match="maps to both"):
        PackSpec.model_validate(_spec(concepts=concepts))


def test_unknown_unit_kind_is_rejected() -> None:
    concepts = [ConceptSpec(name="a", description="", unit_kind="weight", tags=("t:A",))]
    with pytest.raises(ValidationError, match="unit kind"):
        PackSpec.model_validate(_spec(concepts=concepts))


@pytest.mark.parametrize("bad", ["../finance", "Finance", "fin-ance", ""])
def test_bad_pack_ids_are_rejected(bad: str) -> None:
    with pytest.raises(PackError):
        load_spec(bad, REPO / "packs")


def test_missing_pack(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="missing pack file"):
        load_spec("nothere", tmp_path)
