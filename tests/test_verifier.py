from decimal import Decimal

import pytest

from verified_rag.verify.numbers import find_numbers, matches
from verified_rag.verify.verifier import Claim, DraftAnswer, Evidence, verify

REV_24 = Evidence(
    ref="f_2",
    value=Decimal("512341000000"),
    unit="USD",
    years=frozenset({2024}),
    entity_names=frozenset({"example corp", "exmp", "example"}),
    description="Example revenue FY2024",
)
REV_23 = Evidence(
    ref="f_1",
    value=Decimal("500000000000"),
    unit="USD",
    years=frozenset({2023}),
    entity_names=REV_24.entity_names,
    description="Example revenue FY2023",
)
GROWTH = Evidence(
    ref="calc_1",
    value=Decimal("2.4682"),
    unit="pure",
    years=frozenset({2023, 2024}),
    entity_names=REV_24.entity_names,
    description="(a - b) / b * 100",
)
EPS = Evidence(
    ref="f_3",
    value=Decimal("4.25"),
    unit="USD/shares",
    years=frozenset({2024}),
    entity_names=REV_24.entity_names,
    description="Example eps_diluted FY2024",
)
ALL = {e.ref: e for e in (REV_24, REV_23, GROWTH, EPS)}
SEEN = set(ALL)


def _draft(answer: str, *claims: tuple[str, list[str]]) -> DraftAnswer:
    return DraftAnswer(
        answer_text=answer, claims=[Claim(text=t, evidence=refs) for t, refs in claims]
    )


# --- number reading ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "amount", "percent", "is_year"),
    [
        ("$512.3 billion", Decimal("512.3e9"), False, False),
        ("512,341 million", Decimal("512341e6"), False, False),
        ("$512,341,000,000", Decimal("512341000000"), False, False),
        ("2.47%", Decimal("2.47"), True, False),
        ("$4.25", Decimal("4.25"), False, False),
        ("2024", Decimal(2024), False, True),
        ("$512B", Decimal("512e9"), False, False),
    ],
)
def test_find_numbers(text: str, amount: Decimal, percent: bool, is_year: bool) -> None:
    [n] = find_numbers(text)
    assert n.amount == amount
    assert n.percent == percent
    assert n.is_year == is_year


def test_labels_are_not_numbers() -> None:
    assert find_numbers("the FY2024 10-K and Q1 results") == []


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("$512.3 billion", True),
        ("$512 billion", True),
        ("$512.34 billion", True),
        ("$512.4 billion", False),  # wrong at its own precision
        ("$510 billion", False),
        ("$0.51 trillion", True),
        ("512,341 million", True),
        ("-$512.3 billion", False),  # wrong sign
    ],
)
def test_matching_respects_written_precision(text: str, ok: bool) -> None:
    [n] = find_numbers(text)
    assert matches(n, REV_24.value, "USD") is ok


def test_percent_matches_ratio_either_way() -> None:
    [n] = find_numbers("2.47%")
    assert matches(n, Decimal("2.4682"), "pure")
    assert matches(n, Decimal("0.024682"), "pure")
    assert not matches(n, Decimal("2.47"), "USD")


# --- verification ------------------------------------------------------------------------------


def test_good_answer_passes() -> None:
    draft = _draft(
        "Example Corp's revenue was $512.3 billion in fiscal 2024, up 2.47% from fiscal 2023.",
        ("Revenue was $512.3 billion in fiscal 2024", ["f_2"]),
        ("Revenue grew 2.47% from fiscal 2023 to 2024", ["calc_1"]),
    )
    result = verify(draft, ALL, SEEN)
    assert result.ok, result.reasons


@pytest.mark.parametrize(
    ("draft", "reason"),
    [
        (
            _draft("Example revenue was $520 billion.", ("Revenue was $520 billion", ["f_2"])),
            "does not match",
        ),
        (
            _draft("Example revenue was $512.3 billion.", ("Revenue was $512.3 billion", [])),
            "no evidence",
        ),
        (
            _draft("Example revenue was $512.3 billion.", ("Revenue was $512.3 billion", ["f_99"])),
            "not returned by any tool",
        ),
        (
            _draft(
                "Example revenue was $512.3 billion in 2022.",
                ("Revenue was $512.3 billion in 2022", ["f_2"]),
            ),
            "year 2022",
        ),
        (
            # An extra figure in the prose that no claim covers.
            _draft(
                "Example revenue was $512.3 billion and margins were 31%.",
                ("Revenue was $512.3 billion", ["f_2"]),
            ),
            "31%",
        ),
        (
            _draft("Revenue was $512.3 billion.", ("Revenue was $512.3 billion", ["f_2"])),
            "doesn't name the company",
        ),
        (
            _draft("Example revenue grew.", ("Revenue grew", ["f_2"])),
            "must state a number",
        ),
        (
            _draft("Example EPS was 4.25%.", ("EPS was 4.25%", ["f_3"])),
            "does not match",
        ),
    ],
)
def test_bad_answers_fail(draft: DraftAnswer, reason: str) -> None:
    result = verify(draft, ALL, SEEN)
    assert not result.ok
    assert any(reason in r for r in result.reasons), result.reasons


def test_ref_must_have_been_seen_in_this_request() -> None:
    draft = _draft("Example revenue was $512.3 billion.", ("Revenue was $512.3 billion", ["f_2"]))
    assert not verify(draft, ALL, seen_refs={"f_1"}).ok


def test_textual_claims_fail_until_text_path() -> None:
    draft = DraftAnswer(
        answer_text="Example Corp grew because of services.",
        claims=[Claim(text="Growth came from services", type="textual", evidence=["f_2"])],
    )
    result = verify(draft, ALL, SEEN)
    assert not result.ok
    assert "M3" in result.reasons[0]
