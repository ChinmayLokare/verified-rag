from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.conftest import fixture_transport, load_finance_connector_module, no_sleep
from verified_rag.ingest.normalize import wanted_tags
from verified_rag.packs.interface import EntityConfig, PackContext, PackSpec

edgar: Any = load_finance_connector_module()


def _connector(
    spec: PackSpec, raw_dir: Path, transport: httpx.MockTransport | None, offline: bool = False
) -> Any:
    ctx = PackContext(raw_dir, frozenset(spec.document_types), offline=offline)
    settings = edgar.EdgarSettings(sec_user_agent="tests tests@example.com", max_attempts=3)
    client = httpx.Client(transport=transport) if transport else None
    return edgar.EdgarConnector(ctx, settings, client=client, sleep=no_sleep)


def test_fetch_entity_parses_fixture(finance_spec: PackSpec, tmp_path: Path) -> None:
    conn = _connector(finance_spec, tmp_path, fixture_transport())
    batch = conn.fetch_entity(
        EntityConfig(ref="EXMP", aliases=("Example",)), wanted_tags(finance_spec)
    )

    assert batch.entity.external_id == "0000000001"
    assert batch.entity.name == "Example Corp"
    assert batch.entity.fiscal_year_end == "0928"
    assert {"EXMP", "Example"} <= set(batch.entity.aliases)

    docs = {d.external_id: d for d in batch.documents}
    # The 8-K is not a wanted document type; the 2022 10-K comes from the older index page.
    assert set(docs) == {
        "0000000001-22-000030",
        "0000000001-23-000010",
        "0000000001-24-000005",
        "0000000001-24-000020",
    }
    assert docs["0000000001-22-000030"].period_end == date(2022, 9, 24)
    assert docs["0000000001-24-000020"].source_uri.endswith(
        "/1/000000000124000020/exmp-20240928.htm"
    )

    # dei share count is not a wanted tag; the 8-K fact is filtered out.
    assert all(f.tag.startswith("us-gaap:") for f in batch.facts)
    assert all(f.document_external_id != "0000000001-24-000099" for f in batch.facts)
    eps = next(f for f in batch.facts if f.tag == "us-gaap:EarningsPerShareDiluted")
    assert eps.value == Decimal("1.23")  # exact, not a float


def test_raw_responses_are_cached_for_offline_runs(finance_spec: PackSpec, tmp_path: Path) -> None:
    tags = wanted_tags(finance_spec)
    online = _connector(finance_spec, tmp_path, fixture_transport()).fetch_entity(
        EntityConfig(ref="EXMP"), tags
    )
    assert (tmp_path / "0000000001" / "companyfacts.json").is_file()
    offline = _connector(finance_spec, tmp_path, None, offline=True).fetch_entity(
        EntityConfig(ref="EXMP"), tags
    )
    assert offline == online


def test_retries_transient_errors(finance_spec: PackSpec, tmp_path: Path) -> None:
    transport = fixture_transport({"/api/xbrl/companyfacts/CIK0000000001.json": 2})
    batch = _connector(finance_spec, tmp_path, transport).fetch_entity(
        EntityConfig(ref="EXMP"), wanted_tags(finance_spec)
    )
    assert batch.facts


def test_gives_up_after_max_attempts(finance_spec: PackSpec, tmp_path: Path) -> None:
    transport = fixture_transport({"/api/xbrl/companyfacts/CIK0000000001.json": 5})
    with pytest.raises(edgar.EdgarError, match="503"):
        _connector(finance_spec, tmp_path, transport).fetch_entity(
            EntityConfig(ref="EXMP"), wanted_tags(finance_spec)
        )


def test_unknown_ticker(finance_spec: PackSpec, tmp_path: Path) -> None:
    with pytest.raises(edgar.EdgarError, match="unknown ticker"):
        _connector(finance_spec, tmp_path, fixture_transport()).fetch_entity(
            EntityConfig(ref="NOPE"), wanted_tags(finance_spec)
        )


def test_user_agent_with_contact_is_required(
    finance_spec: PackSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VRAG_FINANCE_SEC_USER_AGENT", raising=False)
    ctx = PackContext(tmp_path, frozenset(finance_spec.document_types))
    conn = edgar.EdgarConnector(ctx, edgar.EdgarSettings(_env_file=None, sec_user_agent="anon"))
    with pytest.raises(edgar.EdgarError, match="VRAG_FINANCE_SEC_USER_AGENT"):
        conn.fetch_entity(EntityConfig(ref="EXMP"), wanted_tags(finance_spec))
