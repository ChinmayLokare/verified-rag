from datetime import date

import pytest

from verified_rag.ingest.periods import FiscalPeriod, FiscalYearEnd, derive, fiscal_year_of

SEPT = FiscalYearEnd.parse("0928")  # 52/53-week year ending late September
JUNE = FiscalYearEnd.parse("0630")
JAN = FiscalYearEnd.parse("0131")
DEC = FiscalYearEnd.parse(None)


@pytest.mark.parametrize(
    ("start", "end", "fye", "expected"),
    [
        # 53-week year ending a few days after the nominal date
        (date(2022, 9, 25), date(2023, 9, 30), SEPT, FiscalPeriod(2023, "FY")),
        # 52-week year ending a few days before it
        (date(2023, 10, 1), date(2024, 9, 28), SEPT, FiscalPeriod(2024, "FY")),
        (date(2023, 10, 1), date(2023, 12, 30), SEPT, FiscalPeriod(2024, "Q1")),
        (date(2023, 12, 31), date(2024, 3, 30), SEPT, FiscalPeriod(2024, "Q2")),
        (date(2023, 10, 1), date(2024, 3, 30), SEPT, FiscalPeriod(2024, "H1")),
        (date(2023, 10, 1), date(2024, 6, 29), SEPT, FiscalPeriod(2024, "9M")),
        # June year end: the year ending June 2024 is FY2024; its Q1 ends Sept 2023
        (date(2023, 7, 1), date(2024, 6, 30), JUNE, FiscalPeriod(2024, "FY")),
        (date(2023, 7, 1), date(2023, 9, 30), JUNE, FiscalPeriod(2024, "Q1")),
        (date(2024, 4, 1), date(2024, 6, 30), JUNE, FiscalPeriod(2024, "Q4")),
        # January year end: the year ending January 2025 is FY2025
        (date(2024, 2, 1), date(2025, 1, 31), JAN, FiscalPeriod(2025, "FY")),
        (date(2024, 2, 1), date(2024, 4, 30), JAN, FiscalPeriod(2025, "Q1")),
        # Calendar year
        (date(2024, 1, 1), date(2024, 12, 31), DEC, FiscalPeriod(2024, "FY")),
        (date(2024, 7, 1), date(2024, 9, 30), DEC, FiscalPeriod(2024, "Q3")),
        # Irregular duration: year known, period not
        (date(2024, 1, 1), date(2024, 2, 15), DEC, FiscalPeriod(2024, None)),
    ],
)
def test_durations(start: date, end: date, fye: FiscalYearEnd, expected: FiscalPeriod) -> None:
    assert derive(start, end, fye) == expected


@pytest.mark.parametrize(
    ("end", "fye", "expected"),
    [
        (date(2023, 9, 30), SEPT, FiscalPeriod(2023, "FY")),  # year-end balance sheet
        (date(2023, 12, 30), SEPT, FiscalPeriod(2024, "Q1")),
        (date(2024, 6, 29), SEPT, FiscalPeriod(2024, "Q3")),
        (date(2024, 12, 31), DEC, FiscalPeriod(2024, "FY")),
        (date(2025, 1, 31), JAN, FiscalPeriod(2025, "FY")),
    ],
)
def test_instants(end: date, fye: FiscalYearEnd, expected: FiscalPeriod) -> None:
    assert derive(None, end, fye) == expected


def test_early_january_year_end_means_calendar_year() -> None:
    # EDGAR lists some 52/53-week calendar-style years as ending "0103".
    early_jan = FiscalYearEnd.parse("0103")
    assert early_jan == DEC
    assert derive(date(2023, 1, 2), date(2023, 12, 31), early_jan) == FiscalPeriod(2023, "FY")
    assert derive(date(2020, 12, 30), date(2022, 1, 2), early_jan).fiscal_year == 2021
    assert derive(date(2023, 1, 2), date(2023, 4, 2), early_jan) == FiscalPeriod(2023, "Q1")


def test_december_year_end_tolerance_crosses_new_year() -> None:
    # A 52/53-week year "ending December" can end on Jan 2 of the next calendar year.
    assert fiscal_year_of(date(2016, 1, 2), DEC) == 2015
    assert fiscal_year_of(date(2016, 1, 9), DEC) == 2016


@pytest.mark.parametrize("bad", ["13", "1301", "0000", "ab12"])
def test_bad_fiscal_year_end(bad: str) -> None:
    with pytest.raises(ValueError, match="fiscal year end"):
        FiscalYearEnd.parse(bad)
