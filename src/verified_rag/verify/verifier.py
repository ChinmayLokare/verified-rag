"""Check a drafted answer against its evidence (design.md section 8.5). Pure; no frameworks.

Numeric claims are checked by code:
- every cited reference must exist and must have been returned by a tool in this request;
- every amount written in the claim must match a cited value at its written precision;
- every year written in the claim must be a year of the cited evidence;
- the claim (or the answer) must name an entity the evidence is about.
Answer-level rule: every amount and year in ``answer_text`` must be covered by a claim that
passed, so no unchecked figure can ride along in the prose.

Textual claims need document passages, which arrive with the text path (M3). Until then they
fail with a clear reason.

Limitation (design.md 8.5): this proves the answer matches the evidence, not that the
evidence was the right evidence for the question.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from verified_rag.verify.numbers import WrittenNumber, find_numbers, matches


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    type: Literal["numeric", "textual"] = "numeric"
    evidence: list[str] = Field(default_factory=list[str], max_length=10)


class DraftAnswer(BaseModel):
    """What the agent submits through ``submit_answer`` (ADR-5)."""

    model_config = ConfigDict(extra="forbid")

    answer_text: str = Field(min_length=1, max_length=3000)
    claims: list[Claim] = Field(min_length=1, max_length=20)


@dataclass(frozen=True)
class Evidence:
    """A fact or calculation, flattened for checking."""

    ref: str
    value: Decimal  # scaled
    unit: str
    years: frozenset[int]  # fiscal years and period-end years involved
    entity_names: frozenset[str]  # names and aliases, lowercase
    description: str  # e.g. "Apple Inc. revenue FY2024"


@dataclass
class ClaimCheck:
    claim: Claim
    ok: bool
    reasons: list[str] = field(default_factory=list[str])


@dataclass
class VerificationResult:
    ok: bool
    checks: list[ClaimCheck]
    answer_problems: list[str]

    @property
    def reasons(self) -> list[str]:
        out = [f"claim {c.claim.text!r}: {r}" for c in self.checks for r in c.reasons]
        return out + self.answer_problems


def _mentions(text: str, names: Collection[str]) -> bool:
    lowered = f" {text.lower()} "
    return any(f"{n}" in lowered for n in names if len(n) >= 2)


def _check_numbers(
    numbers: list[WrittenNumber], evidence: list[Evidence], reasons: list[str], where: str
) -> None:
    years = frozenset[int]().union(*(e.years for e in evidence))
    for n in numbers:
        if n.is_year:
            if int(n.value) not in years:
                reasons.append(f"year {n.text} in {where} is not a year of the evidence")
        elif not any(matches(n, e.value, e.unit) for e in evidence):
            reasons.append(f"{n.text} in {where} does not match any cited value")


def verify(
    draft: DraftAnswer,
    evidence: Mapping[str, Evidence],
    seen_refs: Collection[str],
) -> VerificationResult:
    checks: list[ClaimCheck] = []
    for claim in draft.claims:
        check = ClaimCheck(claim, ok=False)
        checks.append(check)
        if claim.type == "textual":
            check.reasons.append("textual claims can't be verified yet (text path is M3)")
            continue
        if not claim.evidence:
            check.reasons.append("no evidence cited")
            continue
        cited: list[Evidence] = []
        for ref in claim.evidence:
            if ref not in seen_refs:
                check.reasons.append(f"{ref} was not returned by any tool in this request")
            elif ref not in evidence:
                check.reasons.append(f"{ref} does not exist")
            else:
                cited.append(evidence[ref])
        if check.reasons:
            continue
        numbers = find_numbers(claim.text)
        if not any(not n.is_year for n in numbers):
            check.reasons.append("a numeric claim must state a number")
        _check_numbers(numbers, cited, check.reasons, "the claim")
        names = frozenset[str]().union(*(e.entity_names for e in cited))
        if not (_mentions(claim.text, names) or _mentions(draft.answer_text, names)):
            check.reasons.append("the answer doesn't name the company the evidence is about")
        check.ok = not check.reasons

    verified = [evidence[r] for c in checks if c.ok for r in c.claim.evidence]
    answer_problems: list[str] = []
    _check_numbers(find_numbers(draft.answer_text), verified, answer_problems, "the answer text")
    ok = bool(checks) and all(c.ok for c in checks) and not answer_problems
    return VerificationResult(ok=ok, checks=checks, answer_problems=answer_problems)
