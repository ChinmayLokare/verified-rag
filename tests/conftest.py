import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from verified_rag.config import Settings
from verified_rag.packs.interface import Connector, Pack, PackContext, PackSpec
from verified_rag.packs.loader import load_spec
from verified_rag.store import migrate


@pytest.fixture(scope="session")
def admin_url() -> str:
    """URL of the dev/CI Postgres. Integration tests are skipped when it is unreachable."""
    url = Settings().database_url
    try:
        with psycopg.connect(url, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up")
    return url


@pytest.fixture
def fresh_db_url(admin_url: str) -> Iterator[str]:
    """A throwaway empty database, dropped after the test."""
    name = f"vrag_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


# --- facts path helpers ----------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "edgar"

_ROUTES = {
    "/files/company_tickers.json": "company_tickers.json",
    "/submissions/CIK0000000001.json": "submissions.json",
    "/submissions/CIK0000000001-submissions-001.json": "CIK0000000001-submissions-001.json",
    "/api/xbrl/companyfacts/CIK0000000001.json": "companyfacts.json",
}


def no_sleep(_seconds: float) -> None:
    pass


def load_finance_connector_module() -> ModuleType:
    path = REPO / "packs" / "finance" / "connector.py"
    spec = importlib.util.spec_from_file_location("finance_connector_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_transport(fail_first: dict[str, int] | None = None) -> httpx.MockTransport:
    """Serves the EDGAR fixtures. ``fail_first`` maps a path to how many 503s to return first."""
    failures = dict(fail_first or {})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if failures.get(path, 0) > 0:
            failures[path] -= 1
            return httpx.Response(503)
        name = _ROUTES.get(path)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, content=(FIXTURES / name).read_bytes())

    return httpx.MockTransport(handler)


@pytest.fixture
def finance_spec() -> PackSpec:
    return load_spec("finance", REPO / "packs")


@pytest.fixture
def fixture_pack(finance_spec: PackSpec, tmp_path: Path) -> tuple[Pack, PackContext]:
    """The real finance pack spec, with a connector that reads the fixtures."""
    module = load_finance_connector_module()
    ctx = PackContext(raw_dir=tmp_path, document_types=frozenset(finance_spec.document_types))

    def factory(ctx: PackContext) -> Connector:
        settings = module.EdgarSettings(sec_user_agent="tests tests@example.com")
        client = httpx.Client(transport=fixture_transport())
        return module.EdgarConnector(ctx, settings, client=client, sleep=no_sleep)

    return Pack(spec=finance_spec, root=REPO / "packs" / "finance", create_connector=factory), ctx


@pytest.fixture
def migrated_db_url(fresh_db_url: str) -> str:
    migrate.apply_pending(fresh_db_url)
    return fresh_db_url
