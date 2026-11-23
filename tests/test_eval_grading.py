from decimal import Decimal

from evals.harness.grade import grade, summarize


def test_grades() -> None:
    assert (
        grade("lookup", "answered", "Example revenue was $1.2 thousand.", Decimal(1200), "USD")
        == "correct"
    )
    assert (
        grade("lookup", "answered", "Example revenue was $1,300.", Decimal(1200), "USD") == "wrong"
    )
    assert grade("lookup", "refused", None, Decimal(1200), "USD") == "refused"
    assert (
        grade("growth", "answered", "Revenue grew 18.8% in 2024.", Decimal("18.8119"), "pure")
        == "correct"
    )
    assert grade("refuse", "refused", None, None, None) == "correct_refusal"
    assert grade("refuse", "answered", "Buy it.", None, None) == "wrong_answered"


def test_year_in_answer_is_not_mistaken_for_the_value() -> None:
    assert (
        grade("lookup", "answered", "In fiscal 2024 revenue was $9.", Decimal(2024), "USD")
        == "wrong"
    )


def test_summary_rates() -> None:
    s = summarize(
        [
            ("lookup", "correct"),
            ("lookup", "correct"),
            ("lookup", "wrong"),
            ("lookup", "refused"),
            ("refuse", "correct_refusal"),
        ]
    )
    assert (s.answerable, s.answered, s.correct, s.wrong) == (4, 3, 2, 1)
    assert s.coverage == 0.75
    assert round(s.wrong_answer_rate, 4) == 0.3333
    assert s.correct_refusals == 1
