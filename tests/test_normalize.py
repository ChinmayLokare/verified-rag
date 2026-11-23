from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from tests.conftest import fixture_transport, load_finance_connector_module, no_sleep
from verified_rag.ingest.normalize import normalize, wanted_tags
from verified_rag.packs.interface import (
    DocumentRecord,
    EntityBatch,
    EntityConfig,
    EntityRecord,
    PackContext,
    PackSpec,
    SourceFact,
)


def _fixture_batch(spec: PackSpec, tmp_path: Path) -> EntityBatch:
    edgar = load_finance_connector_module()
    ctx = PackContext(tmp_path, frozenset(spec.document_types))
    settings = edgar.EdgarSettings(sec_user_agent="tests tests@example.com")
    client = httpx.Client(transport=fixture_transport())
    connector = edgar.EdgarConnector(ctx, settings, client=client, sleep=no_sleep)
    return connector.fetch_entity(EntityConfig(ref="EXMP"), wanted_tags(spec))


def test_fixture_normalization(finance_spec: PackSpec, tmp_path: Path) -> None:
    result = normalize(_fixture_batch(finance_spec, tmp_path), finance_spec)
    report = result.report
    by_doc = {d.record.external_id: d for d in result.documents}
    annual = by_doc["0000000001-24-000020"]

    revenue_24 = [
        f for f in annual.facts if f.concept == "revenue" and f.period_end == date(2024, 9, 28)
    ]
    # Revenues (priority 0) beats RevenueFromContract... (priority 1); the frame repeat collapses.
    assert len(revenue_24) == 1
    assert revenue_24[0].value == Decimal(1200)
    assert revenue_24[0].source_tag == "us-gaap:Revenues"
    assert (revenue_24[0].fiscal_year, revenue_24[0].fiscal_period) == (2024, "FY")
    assert report.superseded == 1
    assert report.duplicates == 1

    # Two different gross profit values for one period: dropped, reported.
    assert not [f for f in annual.facts if f.concept == "gross_profit"]
    assert len(report.conflicts) == 1
    assert "gross_profit" in report.conflicts[0]

    assert report.bad_unit == 1  # the EUR assets fact
    assert report.fiscal_year_mismatches == []

    q1 = by_doc["0000000001-24-000005"].facts
    assert [(f.concept, f.fiscal_year, f.fiscal_period) for f in q1] == [("revenue", 2024, "Q1")]

    assets = [f for f in annual.facts if f.concept == "total_assets"]
    assert {(f.period_type, f.fiscal_year, f.fiscal_period) for f in assets} == {
        ("instant", 2023, "FY"),
        ("instant", 2024, "FY"),
    }


def test_content_hash_is_stable(finance_spec: PackSpec, tmp_path: Path) -> None:
    batch = _fixture_batch(finance_spec, tmp_path)
    a = normalize(batch, finance_spec)
    b = normalize(batch, finance_spec)
    assert [d.content_hash for d in a.documents] == [d.content_hash for d in b.documents]


def test_reported_fiscal_year_mismatch_is_flagged(finance_spec: PackSpec) -> None:
    doc = DocumentRecord(
        external_id="D1",
        doc_type="10-K",
        period_end=date(2024, 9, 28),
        filed_at=None,
        source_uri="x",
    )
    fact = SourceFact(
        document_external_id="D1",
        tag="us-gaap:Revenues",
        unit="USD",
        value=Decimal(1),
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
        reported_fiscal_year=2023,  # wrong: the year ending Sept 2024 is FY2024
        reported_fiscal_period="FY",
    )
    batch = EntityBatch(
        entity=EntityRecord(external_id="E", name="E", fiscal_year_end="0928"),
        documents=(doc,),
        facts=(fact,),
    )
    result = normalize(batch, finance_spec)
    assert len(result.report.fiscal_year_mismatches) == 1
