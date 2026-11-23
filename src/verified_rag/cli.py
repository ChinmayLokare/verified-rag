"""Command-line entry point: ``vrag <command>``.

M1 commands answer numeric questions deterministically from the facts table: resolve the entity
and concept, run a fixed template, and print every value with its reference and source filing.
Natural-language questions arrive with the agent in M2.

Exit codes: 0 ok, 1 error, 2 ambiguous or not found (the CLI refuses rather than guesses).
"""

import argparse
import sys
from collections.abc import Sequence
from decimal import Decimal

import psycopg

from verified_rag.config import Settings, get_settings
from verified_rag.ingest.normalize import NormalizeReport
from verified_rag.ingest.pipeline import IngestReport, ingest
from verified_rag.packs.interface import PackContext
from verified_rag.packs.loader import load_pack
from verified_rag.refs import RefError, parse_ref
from verified_rag.store import migrate
from verified_rag.store.db import QueryLimits, connect
from verified_rag.store.templates import (
    CompareEntitiesParams,
    FactRow,
    FactSourceParams,
    LookupParams,
    QueryResult,
    TimeSeriesParams,
    run_template,
)
from verified_rag.tools.calculate import calculate
from verified_rag.tools.calculator import CalculationError
from verified_rag.tools.resolve import (
    ConceptCandidate,
    EntityCandidate,
    resolve_concept,
    resolve_entity,
    unambiguous,
)

PERIODS = ["FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M"]


class Refusal(Exception):
    """The CLI can't answer without guessing."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vrag", description="VerifiedRAG command line")
    parser.add_argument("--pack", default="finance", help="domain pack (default: finance)")
    commands = parser.add_subparsers(dest="command", required=True)

    m = commands.add_parser("migrate", help="apply pending database migrations")
    m.add_argument("--status", action="store_true", help="list applied and pending migrations")

    i = commands.add_parser("ingest", help="fetch and load facts for the pack's entities")
    i.add_argument("entities", nargs="*", help="entity refs (default: all in pack.yaml)")
    i.add_argument("--offline", action="store_true", help="use only the local raw store")
    i.add_argument("-v", "--verbose", action="store_true", help="print conflicts and checks")

    re_ = commands.add_parser("resolve-entity", help="show entity candidates for a name")
    re_.add_argument("name")
    rc = commands.add_parser("resolve-concept", help="show concept candidates for a phrase")
    rc.add_argument("phrase")

    lk = commands.add_parser("lookup", help="one figure, e.g. lookup AAPL revenue --year 2024")
    lk.add_argument("entity")
    lk.add_argument("concept")
    lk.add_argument("--year", type=int, required=True)
    lk.add_argument("--period", choices=PERIODS, default="FY")

    ts = commands.add_parser("series", help="a figure across fiscal years")
    ts.add_argument("entity")
    ts.add_argument("concept")
    ts.add_argument("--from", dest="from_year", type=int, required=True)
    ts.add_argument("--to", dest="to_year", type=int, required=True)
    ts.add_argument("--period", choices=PERIODS, default="FY")

    cp = commands.add_parser("compare", help="one concept for several entities")
    cp.add_argument("concept")
    cp.add_argument("--entities", nargs="+", required=True)
    cp.add_argument("--from", dest="from_year", type=int, required=True)
    cp.add_argument("--to", dest="to_year", type=int, required=True)
    cp.add_argument("--period", choices=PERIODS, default="FY")

    calc = commands.add_parser(
        "calc", help='arithmetic on references, e.g. calc "(a - b) / b * 100" a=f_12 b=f_9'
    )
    calc.add_argument("expression")
    calc.add_argument("inputs", nargs="+", metavar="NAME=REF")

    src = commands.add_parser("source", help="where a fact came from, e.g. source f_12")
    src.add_argument("ref")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    try:
        if args.command == "migrate":
            return _migrate(settings, status=args.status)
        if args.command == "ingest":
            return _ingest(settings, args)
        with connect(settings.database_url) as conn:
            return _query(conn, settings, args)
    except Refusal as exc:
        print(exc, file=sys.stderr)
        return 2
    except psycopg.OperationalError as exc:
        print(f"error: can't reach the database ({exc})", file=sys.stderr)
        print("hint: is Docker running? Try: docker compose up -d", file=sys.stderr)
        return 1
    except (CalculationError, RefError, ValueError, psycopg.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# --- commands --------------------------------------------------------------------------------


def _migrate(settings: Settings, *, status: bool) -> int:
    if status:
        for line in migrate.status(settings.database_url):
            print(line)
        return 0
    applied = migrate.apply_pending(settings.database_url)
    for m in applied:
        print(f"applied {m.filename}")
    if not applied:
        print("database is up to date")
    return 0


def _ingest(settings: Settings, args: argparse.Namespace) -> int:
    pack = load_pack(args.pack, settings.packs_dir)
    ctx = PackContext(
        raw_dir=settings.raw_dir / pack.spec.id,
        document_types=frozenset(pack.spec.document_types),
        offline=args.offline,
    )
    wanted = {e.upper() for e in args.entities}
    entities = [e for e in pack.spec.entities if not wanted or e.ref.upper() in wanted]
    unknown = wanted - {e.ref.upper() for e in entities}
    if unknown:
        print(f"not in {pack.spec.id}/pack.yaml: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    with connect(settings.database_url) as conn:
        report = ingest(conn, pack, ctx, entities)
    _print_ingest(report, verbose=args.verbose)
    return 0 if report.ok else 1


def _print_ingest(report: IngestReport, *, verbose: bool) -> None:
    for r in report.results:
        if not r.ok:
            print(f"FAILED  {r.ref}: {r.error}")
            continue
        n = r.report or NormalizeReport()
        print(
            f"ok      {r.ref} ({r.entity_name}): {r.documents_written} documents written,"
            f" {r.documents_unchanged} unchanged, {r.facts_written} facts;"
            f" {len(n.conflicts)} conflicts, {n.bad_unit} skipped units,"
            f" {len(n.fiscal_year_mismatches)} fiscal-year mismatches"
        )
        if verbose:
            for line in n.conflicts:
                print(f"          conflict: {line}")
            for line in n.fiscal_year_mismatches:
                print(f"          fiscal year: {line}")
    failed = sum(not r.ok for r in report.results)
    print(f"{len(report.results) - failed} succeeded, {failed} failed")


def _query(conn: psycopg.Connection, settings: Settings, args: argparse.Namespace) -> int:
    limits = QueryLimits(settings.query_timeout_ms, settings.query_max_rows)
    pack: str = args.pack

    if args.command == "resolve-entity":
        _print_candidates(resolve_entity(conn, pack, args.name, limits))
        return 0
    if args.command == "resolve-concept":
        _print_candidates(resolve_concept(conn, pack, args.phrase, limits))
        return 0
    if args.command == "calc":
        inputs = dict(_split_input(item) for item in args.inputs)
        result = calculate(conn, args.expression, inputs, limits)
        print(f"{result.ref} = {_fmt(result.value)} {result.unit}")
        print(f"  {result.expression}  with  " + ", ".join(f"{k}={v}" for k, v in inputs.items()))
        return 0
    if args.command == "source":
        ref = parse_ref(args.ref)
        if ref.kind != "fact":
            raise Refusal("source takes a fact reference like f_12")
        result = run_template(conn, FactSourceParams(fact_id=ref.id), limits)
        return _print_facts(result, empty=f"no fact {args.ref}", detail=True)

    if args.command == "compare":
        concept = _pick_concept(conn, pack, args.concept, limits)
        entity_ids = [_pick_entity(conn, pack, e, limits).entity_id for e in args.entities]
        params = CompareEntitiesParams(
            entity_ids=entity_ids,
            concept_id=concept.concept_id,
            from_year=args.from_year,
            to_year=args.to_year,
            fiscal_period=args.period,
        )
        return _print_facts(run_template(conn, params, limits), empty="no matching facts")

    entity = _pick_entity(conn, pack, args.entity, limits)
    concept = _pick_concept(conn, pack, args.concept, limits)
    if args.command == "lookup":
        params = LookupParams(
            entity_id=entity.entity_id,
            concept_id=concept.concept_id,
            fiscal_year=args.year,
            fiscal_period=args.period,
        )
    else:
        params = TimeSeriesParams(
            entity_id=entity.entity_id,
            concept_id=concept.concept_id,
            from_year=args.from_year,
            to_year=args.to_year,
            fiscal_period=args.period,
        )
    return _print_facts(
        run_template(conn, params, limits),
        empty=f"no {concept.name} for {entity.name} in that period",
        detail=args.command == "lookup",
    )


# --- helpers ---------------------------------------------------------------------------------


def _pick_entity(
    conn: psycopg.Connection, pack: str, name: str, limits: QueryLimits
) -> EntityCandidate:
    candidates = resolve_entity(conn, pack, name, limits)
    choice = unambiguous(candidates)
    if choice is None:
        raise Refusal(_ambiguous("entity", name, candidates))
    if choice.score < 1.0:
        print(f"(entity {name!r} -> {choice.name})", file=sys.stderr)
    return choice


def _pick_concept(
    conn: psycopg.Connection, pack: str, phrase: str, limits: QueryLimits
) -> ConceptCandidate:
    candidates = resolve_concept(conn, pack, phrase, limits)
    choice = unambiguous(candidates)
    if choice is None:
        raise Refusal(_ambiguous("concept", phrase, candidates))
    if choice.score < 1.0:
        print(f"(concept {phrase!r} -> {choice.name})", file=sys.stderr)
    return choice


def _ambiguous(
    kind: str, text: str, candidates: Sequence[EntityCandidate | ConceptCandidate]
) -> str:
    if not candidates:
        return f"no {kind} matches {text!r}"
    lines = [f"{kind} {text!r} is ambiguous; candidates:"]
    lines += [f"  {c.name}  (matched {c.matched!r}, score {c.score:.2f})" for c in candidates]
    return "\n".join(lines)


def _print_candidates(candidates: Sequence[EntityCandidate | ConceptCandidate]) -> None:
    if not candidates:
        print("no candidates")
    for c in candidates:
        ident = c.entity_id if isinstance(c, EntityCandidate) else c.concept_id
        print(f"{c.score:.2f}  {c.name}  (id {ident}, matched {c.matched!r})")


def _print_facts(result: QueryResult, *, empty: str, detail: bool = False) -> int:
    if not result.rows:
        raise Refusal(empty)
    for row in result.rows:
        _print_fact(row, detail=detail)
    if result.truncated:
        print(f"(only the first {len(result.rows)} rows shown)")
    return 0


def _print_fact(row: FactRow, *, detail: bool) -> None:
    period = (
        f"{row.period_start} to {row.period_end}" if row.period_start else f"at {row.period_end}"
    )
    label = f"FY{row.fiscal_year} {row.fiscal_period or '?'}"
    filed = row.filed_at.date() if row.filed_at else "?"
    print(
        f"{row.ref:>10}  {row.entity_name}  {row.concept_name}  {label}  "
        f"{_fmt(row.scaled_value)} {row.unit}  ({period})"
    )
    print(f"{'':>10}  source: {row.doc_type} {row.document_external_id}, filed {filed}")
    if detail:
        print(f"{'':>10}  tag: {row.source_tag}  latest reported: {row.is_latest_reported}")
        print(f"{'':>10}  {row.source_uri}")


def _split_input(item: str) -> tuple[str, str]:
    name, sep, ref = item.partition("=")
    if not sep or not name or not ref:
        raise ValueError(f"inputs look like name=f_12, got {item!r}")
    return name.strip(), ref.strip()


def _fmt(value: Decimal) -> str:
    if value.as_tuple().exponent < -6:  # type: ignore[operator]
        value = value.quantize(Decimal("1e-6"))
    return f"{value.normalize():,f}"


if __name__ == "__main__":
    sys.exit(main())
