"""Turn a connector's raw batch into normalized facts per document. Pure: no database.

For each (document, concept, period) at most one fact survives:
- tags are ranked by their order in the pack's concepts.yaml; the best-ranked tag wins;
- identical repeats are collapsed;
- if the best-ranked tag carries different values for the same period, the fact is dropped and
  reported as a conflict. Refusing beats guessing.
Facts in units the pack doesn't accept, or whose unit kind doesn't match the concept, are dropped.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from verified_rag.ingest import periods
from verified_rag.packs.interface import (
    DocumentRecord,
    EntityBatch,
    EntityRecord,
    PackSpec,
    SourceFact,
)

NORMALIZER_VERSION = "2"  # 2: early-January year ends (ADR-9)


@dataclass(frozen=True)
class TagMapping:
    concept: str
    priority: int  # lower wins


@dataclass(frozen=True)
class NormalizedFact:
    concept: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    period_type: str  # instant | duration
    fiscal_year: int
    fiscal_period: str | None
    source_tag: str
    scale: int = 0


@dataclass(frozen=True)
class NormalizedDocument:
    record: DocumentRecord
    facts: tuple[NormalizedFact, ...]
    content_hash: str


@dataclass
class NormalizeReport:
    facts_in: int = 0
    facts_kept: int = 0
    unmapped_tag: int = 0
    no_document: int = 0
    bad_unit: int = 0
    duplicates: int = 0
    superseded: int = 0
    conflicts: list[str] = field(default_factory=list[str])
    fiscal_year_mismatches: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class NormalizedEntity:
    entity: EntityRecord
    documents: tuple[NormalizedDocument, ...]
    report: NormalizeReport


def tag_mappings(spec: PackSpec) -> dict[str, TagMapping]:
    return {
        tag: TagMapping(concept.name, rank)
        for concept in spec.concepts
        for rank, tag in enumerate(concept.tags)
    }


def wanted_tags(spec: PackSpec) -> frozenset[str]:
    return frozenset(tag_mappings(spec))


def parser_version(spec: PackSpec) -> str:
    return f"{spec.parser_version}+core{NORMALIZER_VERSION}"


_Key = tuple[str, str, date | None, date]  # document, concept, start, end


def normalize(batch: EntityBatch, spec: PackSpec) -> NormalizedEntity:
    report = NormalizeReport(facts_in=len(batch.facts))
    mappings = tag_mappings(spec)
    concept_kind = {c.name: c.unit_kind for c in spec.concepts}
    unit_kind = {name: u.kind for name, u in spec.units.items()}
    docs = {d.external_id: d for d in batch.documents if d.doc_type in spec.document_types}
    fye = periods.FiscalYearEnd.parse(batch.entity.fiscal_year_end)

    grouped: dict[_Key, list[tuple[int, SourceFact]]] = defaultdict(list)
    for fact in batch.facts:
        mapping = mappings.get(fact.tag)
        if mapping is None:
            report.unmapped_tag += 1
            continue
        if fact.document_external_id not in docs:
            report.no_document += 1
            continue
        if unit_kind.get(fact.unit) != concept_kind[mapping.concept]:
            report.bad_unit += 1
            continue
        key = (fact.document_external_id, mapping.concept, fact.period_start, fact.period_end)
        grouped[key].append((mapping.priority, fact))

    per_doc: dict[str, list[NormalizedFact]] = defaultdict(list)
    for (doc_id, concept, start, end), candidates in grouped.items():
        best = min(p for p, _ in candidates)
        top = [f for p, f in candidates if p == best]
        report.superseded += len(candidates) - len(top)
        distinct = {(f.value, f.unit) for f in top}
        if len(distinct) > 1:
            values = ", ".join(sorted(f"{v} {u}" for v, u in distinct))
            report.conflicts.append(f"{doc_id} {concept} {start}..{end}: {values}")
            continue
        report.duplicates += len(top) - 1
        fact = top[0]
        derived = periods.derive(start, end, fye)
        _cross_check(fact, derived, docs[doc_id], report)
        per_doc[doc_id].append(
            NormalizedFact(
                concept=concept,
                value=fact.value,
                unit=fact.unit,
                period_start=start,
                period_end=end,
                period_type="instant" if start is None else "duration",
                fiscal_year=derived.fiscal_year,
                fiscal_period=derived.fiscal_period,
                source_tag=fact.tag,
            )
        )

    documents: list[NormalizedDocument] = []
    for doc_id in sorted(per_doc):
        facts = tuple(sorted(per_doc[doc_id], key=_sort_key))
        report.facts_kept += len(facts)
        documents.append(
            NormalizedDocument(docs[doc_id], facts, _content_hash(docs[doc_id], facts))
        )
    return NormalizedEntity(batch.entity, tuple(documents), report)


def _cross_check(
    fact: SourceFact, derived: periods.FiscalPeriod, doc: DocumentRecord, report: NormalizeReport
) -> None:
    """The source's filing-level fiscal year is only trustworthy for the document's own period."""
    if (
        fact.reported_fiscal_year is not None
        and doc.period_end == fact.period_end
        and fact.reported_fiscal_period == derived.fiscal_period
        and fact.reported_fiscal_year != derived.fiscal_year
    ):
        report.fiscal_year_mismatches.append(
            f"{doc.external_id} {fact.tag} ending {fact.period_end}: source says "
            f"{fact.reported_fiscal_period} {fact.reported_fiscal_year}, derived "
            f"{derived.fiscal_period} {derived.fiscal_year}"
        )


def _sort_key(f: NormalizedFact) -> tuple[str, str, str]:
    return (
        f.concept,
        f.period_start.isoformat() if f.period_start else "",
        f.period_end.isoformat(),
    )


def _content_hash(doc: DocumentRecord, facts: tuple[NormalizedFact, ...]) -> str:
    payload = {
        "doc": doc.model_dump(mode="json"),
        "facts": [
            [
                f.concept,
                str(f.value),
                f.unit,
                f.scale,
                f.period_start.isoformat() if f.period_start else None,
                f.period_end.isoformat(),
                f.fiscal_year,
                f.fiscal_period,
                f.source_tag,
            ]
            for f in facts
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
