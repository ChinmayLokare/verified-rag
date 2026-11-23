"""Grade outcomes and summarize. Pure; no model calls.

- Answerable question, answered: correct if any number in the answer states the expected value
  at its written precision (same rule as the verifier); otherwise wrong.
- Answerable question, refused: a refusal. It costs coverage, not correctness.
- Should-refuse question: correct if refused; answering it is wrong.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from verified_rag.verify.numbers import find_numbers, matches

Grade = Literal["correct", "wrong", "refused", "correct_refusal", "wrong_answered"]


def grade(
    kind: str, status: str, answer_text: str | None, expected: Decimal | None, unit: str | None
) -> Grade:
    if kind == "refuse":
        return "correct_refusal" if status == "refused" else "wrong_answered"
    if status != "answered":
        return "refused"
    if expected is None or unit is None:
        raise ValueError("answerable questions need an expected value and unit")
    numbers = [n for n in find_numbers(answer_text or "") if not n.is_year]
    return "correct" if any(matches(n, expected, unit) for n in numbers) else "wrong"


@dataclass(frozen=True)
class Summary:
    answerable: int
    answered: int
    correct: int
    wrong: int
    refuse_questions: int
    correct_refusals: int

    @property
    def coverage(self) -> float:
        return self.answered / self.answerable if self.answerable else 0.0

    @property
    def wrong_answer_rate(self) -> float:
        return self.wrong / self.answered if self.answered else 0.0

    @property
    def accuracy_on_all(self) -> float:
        return self.correct / self.answerable if self.answerable else 0.0


def summarize(grades: list[tuple[str, Grade]]) -> Summary:
    answerable = [g for k, g in grades if k != "refuse"]
    refusals = [g for k, g in grades if k == "refuse"]
    return Summary(
        answerable=len(answerable),
        answered=sum(g in ("correct", "wrong") for g in answerable),
        correct=answerable.count("correct"),
        wrong=answerable.count("wrong"),
        refuse_questions=len(refusals),
        correct_refusals=refusals.count("correct_refusal"),
    )
