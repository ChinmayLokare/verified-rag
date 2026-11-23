"""Find numbers in answer text and compare them with evidence values. Pure; no frameworks.

A written number carries its own precision: "$391.0 billion" claims 391.0e9 to within
0.05e9; "31.51%" claims 31.51 percent to within 0.005. ``matches`` accepts an evidence value
only if rounding it to the written precision gives the written number.

Four-digit integers between 1990 and 2100 with no currency, percent, scale or decimals are
treated as years, not amounts. Numbers glued to letters (FY2024, Q1, 10-K) are labels, not
amounts, and are ignored.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

_SCALES = {
    "thousand": Decimal("1e3"),
    "k": Decimal("1e3"),
    "million": Decimal("1e6"),
    "mn": Decimal("1e6"),
    "m": Decimal("1e6"),
    "billion": Decimal("1e9"),
    "bn": Decimal("1e9"),
    "b": Decimal("1e9"),
    "trillion": Decimal("1e12"),
    "tn": Decimal("1e12"),
    "t": Decimal("1e12"),
}

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.,_])"
    r"(?P<neg>[-\u2212])?(?P<cur>\$)?\s?"  # hyphen or Unicode minus sign
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<pct>%|percent\b|percentage points?\b)"
    r"|\s?(?P<scale>thousand|million|billion|trillion|bn|mn|tn|[kmbt])(?![A-Za-z]))?",
    re.IGNORECASE,
)

YEAR_MIN, YEAR_MAX = 1990, 2100


@dataclass(frozen=True)
class WrittenNumber:
    text: str
    value: Decimal  # as written, before scale
    decimals: int
    scale: Decimal
    percent: bool
    negative: bool
    currency: bool

    @property
    def is_year(self) -> bool:
        return (
            not self.percent
            and not self.currency
            and self.scale == 1
            and self.decimals == 0
            and not self.negative
            and "," not in self.text
            and YEAR_MIN <= self.value <= YEAR_MAX
        )

    @property
    def amount(self) -> Decimal:
        return self.value * self.scale


def find_numbers(text: str) -> list[WrittenNumber]:
    out: list[WrittenNumber] = []
    for m in _NUMBER.finditer(text):
        end = m.end()
        nxt = text[end : end + 1]
        if not m["pct"] and not m["scale"] and (nxt.isalpha() or nxt == "-"):
            continue  # a label such as 10-K or 3rd
        raw = m["num"]
        digits = raw.replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        scale = _SCALES[m["scale"].lower()] if m["scale"] else Decimal(1)
        out.append(
            WrittenNumber(
                text=m.group(0).strip(),
                value=Decimal(digits),
                decimals=decimals,
                scale=scale,
                percent=bool(m["pct"]),
                negative=bool(m["neg"]),
                currency=bool(m["cur"]),
            )
        )
    return out


def _close(target: Decimal, written: Decimal, decimals: int, scale: Decimal) -> bool:
    tolerance = Decimal(5).scaleb(-(decimals + 1)) * scale
    return abs(abs(target) - written * scale) <= tolerance


def matches(written: WrittenNumber, value: Decimal, unit: str) -> bool:
    """Does ``written`` state ``value`` (in ``unit``) at its own precision?"""
    if written.negative and value > 0:
        return False
    if written.percent:
        if unit != "pure":
            return False
        # A ratio may be stored as 0.3151 or, if the expression multiplied by 100, as 31.51.
        return _close(value * 100, written.value, written.decimals, Decimal(1)) or _close(
            value, written.value, written.decimals, Decimal(1)
        )
    return _close(value, written.value, written.decimals, written.scale)
