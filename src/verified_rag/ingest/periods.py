"""Derive a fact's fiscal year and period from its own dates (ADR-9).

Filing-level fields (like companyfacts ``fy``/``fp``) describe the filing, not each fact: a 10-K
also carries prior-year comparatives. So each fact's fiscal period comes from its start and end
dates plus the entity's fiscal year end.

Rules:
- Fiscal year ``Y`` ends on the anchor date ``Y-MM-DD`` (the entity's fiscal year end), give or
  take ``TOLERANCE_DAYS`` for 52/53-week years. A fiscal year is named for the calendar year it
  ends in (Microsoft's year ending June 2024 is FY2024).
- Durations are classified by length: quarter, half, nine months, or full year.
- Quarter number counts quarters from the start of the fiscal year.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

TOLERANCE_DAYS = 7
_QUARTER_DAYS = 365.25 / 4

# (min_days, max_days, label), inclusive.
_DURATIONS: tuple[tuple[int, int, str], ...] = (
    (80, 100, "Q"),
    (172, 192, "H1"),
    (263, 283, "9M"),
    (350, 380, "FY"),
)


@dataclass(frozen=True)
class FiscalYearEnd:
    month: int
    day: int

    @classmethod
    def parse(cls, mmdd: str | None) -> "FiscalYearEnd":
        """Parse ``MMDD`` (the EDGAR format). Missing or blank means calendar year."""
        if not mmdd or not mmdd.strip():
            return cls(12, 31)
        text = mmdd.strip()
        if len(text) != 4 or not text.isdigit():
            raise ValueError(f"fiscal year end must be MMDD, got {mmdd!r}")
        month, day = int(text[:2]), int(text[2:])
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            raise ValueError(f"invalid fiscal year end {mmdd!r}")
        if month == 1 and day <= TOLERANCE_DAYS:
            # A 52/53-week year ending in the first days of January is a calendar-style year
            # that spills over (e.g. "0103"); it is named for the year that just ended.
            return cls(12, 31)
        return cls(month, day)

    def anchor(self, year: int) -> date:
        """Nominal end date of fiscal year ``year``."""
        last = calendar.monthrange(year, self.month)[1]
        return date(year, self.month, min(self.day, last))


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    fiscal_period: str | None  # FY, Q1-Q4, H1, 9M; None if the duration is irregular


def fiscal_year_of(day: date, fye: FiscalYearEnd) -> int:
    """The fiscal year containing ``day``, allowing for 52/53-week year ends."""
    tolerance = timedelta(days=TOLERANCE_DAYS)
    year = day.year
    if day > fye.anchor(year) + tolerance:
        return year + 1
    if day <= fye.anchor(year - 1) + tolerance:
        return year - 1
    return year


def _quarter_number(end: date, fiscal_year: int, fye: FiscalYearEnd) -> int:
    days_in = (end - fye.anchor(fiscal_year - 1)).days
    return max(1, min(4, round(days_in / _QUARTER_DAYS)))


def derive(start: date | None, end: date, fye: FiscalYearEnd) -> FiscalPeriod:
    """Fiscal year and period for an instant (``start`` is None) or a duration."""
    year = fiscal_year_of(end, fye)

    if start is None:
        if abs((end - fye.anchor(year)).days) <= TOLERANCE_DAYS:
            return FiscalPeriod(year, "FY")
        quarter = _quarter_number(end, year, fye)
        return FiscalPeriod(year, f"Q{quarter}" if quarter < 4 else "FY")

    days = (end - start).days + 1
    for low, high, label in _DURATIONS:
        if low <= days <= high:
            if label == "Q":
                return FiscalPeriod(year, f"Q{_quarter_number(end, year, fye)}")
            return FiscalPeriod(year, label)
    return FiscalPeriod(year, None)
