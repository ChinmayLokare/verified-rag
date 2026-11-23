# ADR-9: Derive each fact's fiscal period from its own dates

**Status:** Accepted (M1)

## Context

In EDGAR company-facts data, `fy` and `fp` describe the filing, not the individual fact. A 10-K for fiscal 2024 also reports fiscal 2023 and 2022 comparatives, all tagged `fy=2024`. Using those fields would label prior-year figures as the current year. Companies also use non-calendar years (Apple ends late September, Microsoft in June, Walmart in January), and many use 52/53-week years whose end date drifts by a few days.

## Decision

`verified_rag.ingest.periods` derives `fiscal_year` and `fiscal_period` from each fact's start and end dates plus the entity's fiscal year end (`MMDD`, from EDGAR submissions):

- Fiscal year `Y` ends on the anchor date `Y-MM-DD`, within ±7 days. A fiscal year is named for the calendar year it ends in.
- Duration length picks the period: about 91 days is a quarter, about 182 days is `H1`, about 273 days is `9M`, about 365 days is `FY`. Other lengths keep the year and leave the period empty.
- Quarter number counts quarters from the start of the fiscal year.
- Instants at the year end are `FY`; other instants take their quarter.

The source's `fy` is still used as a cross-check, but only for facts whose end date equals the filing's own report date. Mismatches are counted in the ingest report (`vrag ingest -v` lists them).

A year end in the first `TOLERANCE_DAYS` of January (EDGAR lists Johnson & Johnson as `0103`) is treated as December 31. Those are 52/53-week calendar-style years named for the year that just ended; taking `0103` literally pushed every J&J year forward by one.

## Validation (first real ingest, 2026-11-23)

Ten companies, about 54,000 facts. Fiscal-year cross-check after the January fix:

- 0 mismatches for AAPL, MSFT, NKE, ADBE, AMD, MMM, KO, PEP, JNJ.
- 423 for WMT, all in filings from 2009 to 2014. From 2014 onward SEC's labels agree with ours (the year ending January 2025 is FY2025, as Walmart names it). The older labels used the start-year convention. No override needed.

## Consequences

- Prior-year comparatives get the right year, so restatements line up by period and `is_latest_reported` works.
- Some companies name fiscal years by the calendar year they *start* in (some retailers with January year ends). For those, the derived year is off by one. The cross-check will show it on the first real ingest; if it happens, add a per-entity override in `pack.yaml` rather than changing the rule.
- Odd-length periods (transition periods after a fiscal year change) have no `fiscal_period` and are invisible to templates that filter on it.
