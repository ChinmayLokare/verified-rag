"""Ingestion for the facts path: fetch, normalize, commit (design.md section 7).

Each entity is written in one transaction. A document whose content hash and parser version are
unchanged is skipped, so re-running is safe and cheap. After writing, ``is_latest_reported`` is
recomputed for the entity: for each (concept, period, dimensions) the fact from the most recently
filed document wins.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from verified_rag.ingest.normalize import (
    NormalizedDocument,
    NormalizedEntity,
    NormalizeReport,
    normalize,
    parser_version,
    wanted_tags,
)
from verified_rag.packs.interface import EntityConfig, Pack, PackContext, PackSpec
from verified_rag.store.db import returned_id


@dataclass
class EntityResult:
    ref: str
    ok: bool
    error: str | None = None
    entity_name: str | None = None
    documents_written: int = 0
    documents_unchanged: int = 0
    facts_written: int = 0
    report: NormalizeReport | None = None


@dataclass
class IngestReport:
    results: list[EntityResult] = field(default_factory=list[EntityResult])

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


def sync_concepts(conn: psycopg.Connection, spec: PackSpec) -> dict[str, int]:
    """Upsert the pack's concepts and their curated and tag aliases. Returns name -> concept_id."""
    ids: dict[str, int] = {}
    with conn.transaction():
        for concept in spec.concepts:
            row = conn.execute(
                "INSERT INTO concepts (pack_id, name, description, unit_kind)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (pack_id, name) DO UPDATE"
                " SET description = EXCLUDED.description, unit_kind = EXCLUDED.unit_kind"
                " RETURNING concept_id",
                (spec.id, concept.name, concept.description, concept.unit_kind),
            ).fetchone()
            concept_id = returned_id(row)
            ids[concept.name] = concept_id
            conn.execute(
                "DELETE FROM concept_aliases"
                " WHERE concept_id = %s AND source IN ('curated', 'tag')",
                (concept_id,),
            )
            aliases = dict.fromkeys(a.strip().lower() for a in concept.aliases if a.strip())
            rows = [(concept_id, alias, "curated", 0) for alias in aliases]
            rows += [(concept_id, tag, "tag", rank) for rank, tag in enumerate(concept.tags)]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO concept_aliases (concept_id, alias, source, priority)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT (concept_id, alias) DO NOTHING",
                    rows,
                )
    return ids


def ingest(
    conn: psycopg.Connection,
    pack: Pack,
    ctx: PackContext,
    entities: Sequence[EntityConfig] | None = None,
) -> IngestReport:
    spec = pack.spec
    concept_ids = sync_concepts(conn, spec)
    connector = pack.create_connector(ctx)
    tags = wanted_tags(spec)
    report = IngestReport()
    for entity in entities if entities is not None else spec.entities:
        result = EntityResult(ref=entity.ref, ok=False)
        report.results.append(result)
        try:
            batch = connector.fetch_entity(entity, tags)
            normalized = normalize(batch, spec)
            result.report = normalized.report
            result.entity_name = normalized.entity.name
            write_entity(conn, spec, normalized, concept_ids, result)
            result.ok = True
        except Exception as exc:  # one bad entity must not stop the run
            result.error = f"{exc.__class__.__name__}: {exc}"
    return report


def write_entity(
    conn: psycopg.Connection,
    spec: PackSpec,
    normalized: NormalizedEntity,
    concept_ids: dict[str, int],
    result: EntityResult,
) -> None:
    version = parser_version(spec)
    e = normalized.entity
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO entities (pack_id, external_id, name, aliases, attributes)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (pack_id, external_id) DO UPDATE SET name = EXCLUDED.name,"
            " aliases = EXCLUDED.aliases, attributes = EXCLUDED.attributes"
            " RETURNING entity_id",
            (
                spec.id,
                e.external_id,
                e.name,
                list(e.aliases),
                Jsonb({**e.attributes, "fiscal_year_end": e.fiscal_year_end}),
            ),
        ).fetchone()
        entity_id = returned_id(row)

        existing = {
            str(r[0]): (str(r[1]), str(r[2]), str(r[3]))
            for r in conn.execute(
                "SELECT external_id, content_hash, parser_version, status FROM documents"
                " WHERE pack_id = %s AND entity_id = %s",
                (spec.id, entity_id),
            ).fetchall()
        }
        for doc in normalized.documents:
            if existing.get(doc.record.external_id) == (doc.content_hash, version, "indexed"):
                result.documents_unchanged += 1
                continue
            _write_document(conn, spec.id, entity_id, doc, version, concept_ids)
            result.documents_written += 1
            result.facts_written += len(doc.facts)

        mark_latest(conn, entity_id)


def _write_document(
    conn: psycopg.Connection,
    pack_id: str,
    entity_id: int,
    doc: NormalizedDocument,
    version: str,
    concept_ids: dict[str, int],
) -> None:
    r = doc.record
    row = conn.execute(
        "INSERT INTO documents (pack_id, entity_id, external_id, doc_type, period_end, filed_at,"
        " source_uri, content_hash, parser_version, status, error, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'indexed', NULL, now())"
        " ON CONFLICT (pack_id, external_id) DO UPDATE SET entity_id = EXCLUDED.entity_id,"
        " doc_type = EXCLUDED.doc_type, period_end = EXCLUDED.period_end,"
        " filed_at = EXCLUDED.filed_at, source_uri = EXCLUDED.source_uri,"
        " content_hash = EXCLUDED.content_hash, parser_version = EXCLUDED.parser_version,"
        " status = 'indexed', error = NULL, updated_at = now()"
        " RETURNING document_id",
        (
            pack_id,
            entity_id,
            r.external_id,
            r.doc_type,
            r.period_end,
            r.filed_at,
            r.source_uri,
            doc.content_hash,
            version,
        ),
    ).fetchone()
    document_id = returned_id(row)
    conn.execute("DELETE FROM facts WHERE document_id = %s", (document_id,))
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY facts (pack_id, entity_id, document_id, concept_id, value, unit, scale,"
            " period_start, period_end, period_type, fiscal_year, fiscal_period, source_tag)"
            " FROM STDIN"
        ) as copy,
    ):
        for f in doc.facts:
            copy.write_row(
                (
                    pack_id,
                    entity_id,
                    document_id,
                    concept_ids[f.concept],
                    f.value,
                    f.unit,
                    f.scale,
                    f.period_start,
                    f.period_end,
                    f.period_type,
                    f.fiscal_year,
                    f.fiscal_period,
                    f.source_tag,
                )
            )


def mark_latest(conn: psycopg.Connection, entity_id: int) -> None:
    conn.execute(
        "UPDATE facts SET is_latest_reported = false WHERE entity_id = %s AND is_latest_reported",
        (entity_id,),
    )
    conn.execute(
        "UPDATE facts f SET is_latest_reported = true FROM ("
        "  SELECT DISTINCT ON (f2.concept_id, f2.period_start, f2.period_end, f2.dimensions)"
        "         f2.fact_id"
        "  FROM facts f2 JOIN documents d ON d.document_id = f2.document_id"
        "  WHERE f2.entity_id = %s AND d.status = 'indexed'"
        "  ORDER BY f2.concept_id, f2.period_start, f2.period_end, f2.dimensions,"
        "           d.filed_at DESC NULLS LAST, d.external_id DESC"
        ") latest WHERE f.fact_id = latest.fact_id",
        (entity_id,),
    )
