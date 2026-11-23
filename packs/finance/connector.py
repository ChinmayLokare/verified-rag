"""EDGAR connector for the finance pack.

Sources (all public, no API key):
- ticker to CIK:  https://www.sec.gov/files/company_tickers.json
- filing index:   https://data.sec.gov/submissions/CIK##########.json (+ older pages)
- XBRL facts:     https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

SEC fair-access rules: a descriptive User-Agent with contact details, and at most 10 requests
per second. The User-Agent comes from VRAG_FINANCE_SEC_USER_AGENT (environment or .env), never
from the repo. Raw responses are cached under the raw store so ``offline`` runs need no network.
"""

import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from verified_rag.packs.interface import (
    Connector,
    DocumentRecord,
    EntityBatch,
    EntityConfig,
    EntityRecord,
    PackContext,
    SourceFact,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DATA_URL = "https://data.sec.gov"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

_SUFFIX = re.compile(
    r"[,\s]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc)\.?$", re.IGNORECASE
)
_RETRY_STATUS = {429, 500, 502, 503, 504}


class EdgarError(RuntimeError):
    pass


class EdgarSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VRAG_FINANCE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    sec_user_agent: str = Field(
        default="", description="e.g. 'YourApp your-name your-email@example.com'"
    )
    requests_per_second: float = Field(default=5.0, gt=0, le=10)
    max_attempts: int = Field(default=4, ge=1, le=10)


def _load_json(raw: bytes) -> Any:
    # Floats become Decimal so values like EPS keep their exact digits.
    return json.loads(raw, parse_float=Decimal)


class EdgarConnector:
    def __init__(
        self,
        ctx: PackContext,
        settings: EdgarSettings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ctx = ctx
        self.settings = settings
        self._client = client
        self._sleep = sleep
        self._last_request = 0.0
        self._tickers: dict[str, int] | None = None

    # --- HTTP and cache ----------------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            agent = self.settings.sec_user_agent.strip()
            if "@" not in agent:
                raise EdgarError(
                    "set VRAG_FINANCE_SEC_USER_AGENT to 'AppName contact@example.com' "
                    "(SEC fair-access policy) in the environment or .env"
                )
            self._client = httpx.Client(
                headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    def _get(self, url: str) -> bytes:
        client = self._http()
        delay = 1.0
        for attempt in range(1, self.settings.max_attempts + 1):
            wait = self._last_request + 1.0 / self.settings.requests_per_second - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = client.get(url)
            except httpx.TransportError as exc:
                if attempt == self.settings.max_attempts:
                    raise EdgarError(f"GET {url} failed: {exc}") from exc
            else:
                if response.status_code == 200:
                    return response.content
                if response.status_code not in _RETRY_STATUS:
                    raise EdgarError(f"GET {url} returned HTTP {response.status_code}")
                if attempt == self.settings.max_attempts:
                    raise EdgarError(f"GET {url} kept returning HTTP {response.status_code}")
            self._sleep(delay)
            delay *= 2
        raise EdgarError(f"GET {url} failed")  # unreachable; keeps type checkers happy

    def _cached(self, relative: str, url: str) -> Any:
        path = self.ctx.raw_dir / relative
        if self.ctx.offline:
            if not path.is_file():
                raise EdgarError(f"offline and not cached: {path}")
            return _load_json(path.read_bytes())
        raw = self._get(url)
        data = _load_json(raw)  # validate before caching
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        return data

    # --- lookups -----------------------------------------------------------------------------

    def _cik(self, ref: str) -> int:
        if ref.isdigit():
            return int(ref)
        if self._tickers is None:
            data = cast(
                dict[str, dict[str, Any]], self._cached("company_tickers.json", TICKERS_URL)
            )
            self._tickers = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}
        cik = self._tickers.get(ref.upper())
        if cik is None:
            raise EdgarError(f"unknown ticker {ref!r}")
        return cik

    def _submissions(self, cik: int) -> tuple[dict[str, Any], list[dict[str, list[Any]]]]:
        folder = f"{cik:010d}"
        main = cast(
            dict[str, Any],
            self._cached(
                f"{folder}/submissions.json", f"{DATA_URL}/submissions/CIK{cik:010d}.json"
            ),
        )
        filings = cast(dict[str, Any], main.get("filings", {}))
        pages = [cast(dict[str, list[Any]], filings.get("recent", {}))]
        for extra in cast(list[dict[str, Any]], filings.get("files", [])):
            name = str(extra["name"])
            if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                raise EdgarError(f"unexpected submissions page name {name!r}")
            pages.append(
                cast(
                    dict[str, list[Any]],
                    self._cached(f"{folder}/{name}", f"{DATA_URL}/submissions/{name}"),
                )
            )
        return main, pages

    # --- Connector ---------------------------------------------------------------------------

    def fetch_entity(self, entity: EntityConfig, wanted_tags: frozenset[str]) -> EntityBatch:
        cik = self._cik(entity.ref)
        main, pages = self._submissions(cik)
        companyfacts = cast(
            dict[str, Any],
            self._cached(
                f"{cik:010d}/companyfacts.json",
                f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json",
            ),
        )
        facts = self._facts(companyfacts, wanted_tags)
        documents = self._documents(cik, pages, facts, companyfacts, wanted_tags)

        name = str(main.get("name") or companyfacts.get("entityName") or entity.ref)
        tickers = [str(t) for t in cast(list[Any], main.get("tickers", []))]
        short = _SUFFIX.sub("", name).strip()
        aliases = dict.fromkeys([*tickers, entity.ref.upper(), *entity.aliases, short])
        aliases.pop(name, None)
        return EntityBatch(
            entity=EntityRecord(
                external_id=f"{cik:010d}",
                name=name,
                aliases=tuple(a for a in aliases if a),
                attributes={
                    "ticker": entity.ref.upper(),
                    "cik": cik,
                    "sic": cast(str | None, main.get("sicDescription")),
                },
                fiscal_year_end=cast(str | None, main.get("fiscalYearEnd")) or None,
            ),
            documents=tuple(documents),
            facts=tuple(facts),
        )

    def _facts(self, companyfacts: dict[str, Any], wanted: frozenset[str]) -> list[SourceFact]:
        out: list[SourceFact] = []
        by_ns = cast(dict[str, dict[str, Any]], companyfacts.get("facts", {}))
        for ns, tags in by_ns.items():
            for tag, body in tags.items():
                qualified = f"{ns}:{tag}"
                if qualified not in wanted:
                    continue
                units = cast(dict[str, list[dict[str, Any]]], body.get("units", {}))
                for unit, rows in units.items():
                    for row in rows:
                        if row.get("form") not in self.ctx.document_types:
                            continue
                        start = row.get("start")
                        out.append(
                            SourceFact(
                                document_external_id=str(row["accn"]),
                                tag=qualified,
                                unit=unit,
                                value=Decimal(row["val"]),
                                period_start=date.fromisoformat(start) if start else None,
                                period_end=date.fromisoformat(row["end"]),
                                reported_fiscal_year=row.get("fy"),
                                reported_fiscal_period=row.get("fp"),
                            )
                        )
        return out

    def _documents(
        self,
        cik: int,
        pages: list[dict[str, list[Any]]],
        facts: list[SourceFact],
        companyfacts: dict[str, Any],
        wanted: frozenset[str],
    ) -> list[DocumentRecord]:
        needed = {f.document_external_id for f in facts}
        found: dict[str, DocumentRecord] = {}
        for page in pages:
            accns = page.get("accessionNumber", [])
            for i, accn in enumerate(accns):
                if accn not in needed or accn in found:
                    continue
                form = str(page["form"][i])
                if form not in self.ctx.document_types:
                    continue
                report_date = page.get("reportDate", [None] * len(accns))[i]
                accepted = page.get("acceptanceDateTime", [None] * len(accns))[i]
                primary = page.get("primaryDocument", [""] * len(accns))[i]
                found[accn] = DocumentRecord(
                    external_id=accn,
                    doc_type=form,
                    period_end=date.fromisoformat(report_date) if report_date else None,
                    filed_at=datetime.fromisoformat(accepted) if accepted else None,
                    source_uri=_archive_uri(cik, accn, str(primary or "")),
                )
        # Accessions missing from the submissions index: fall back to what companyfacts says.
        for accn in needed - found.keys():
            meta = _companyfacts_filing(companyfacts, accn, wanted)
            if meta is None:
                continue
            form, filed = meta
            found[accn] = DocumentRecord(
                external_id=accn,
                doc_type=form,
                period_end=None,
                filed_at=datetime.combine(date.fromisoformat(filed), datetime.min.time(), UTC),
                source_uri=_archive_uri(cik, accn, ""),
            )
        return sorted(found.values(), key=lambda d: d.external_id)


def _archive_uri(cik: int, accn: str, primary: str) -> str:
    folder = f"{ARCHIVES_URL}/{cik}/{accn.replace('-', '')}"
    return f"{folder}/{primary}" if primary else f"{folder}/{accn}-index.htm"


def _companyfacts_filing(
    companyfacts: dict[str, Any], accn: str, wanted: frozenset[str]
) -> tuple[str, str] | None:
    by_ns = cast(dict[str, dict[str, Any]], companyfacts.get("facts", {}))
    for ns, tags in by_ns.items():
        for tag, body in tags.items():
            if f"{ns}:{tag}" not in wanted:
                continue
            for rows in cast(dict[str, list[dict[str, Any]]], body.get("units", {})).values():
                for row in rows:
                    if row.get("accn") == accn:
                        return str(row["form"]), str(row["filed"])
    return None


def create_connector(ctx: PackContext) -> Connector:
    return EdgarConnector(ctx, EdgarSettings())
