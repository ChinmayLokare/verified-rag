"""Input guard, deterministic rules first (design.md 8.1). A model-based classifier comes later.

Returns a refusal reason, or None when the question may go to the agent.
"""

import re

MAX_QUESTION_CHARS = 1000

_OUT_OF_SCOPE = [
    # Advice phrasing only: "should I buy", not "spent to buy back shares".
    (
        re.compile(
            r"\b(should|would you|is it (a )?(good|bad|smart))\b.*\b(buy|sell|hold|invest)", re.I
        ),
        "investment advice",
    ),
    (
        re.compile(
            r"\b(invest(ment)? advice|recommend(ation)?s?|price target|undervalued|overvalued)\b",
            re.I,
        ),
        "investment advice",
    ),
    (
        re.compile(
            r"\b(forecast|predict(ion)?|projection|next (year|quarter)|will .* (be|grow|reach))\b",
            re.I,
        ),
        "a forecast",
    ),
    (re.compile(r"\b(stock|share) price\b", re.I), "market prices, which filings don't contain"),
]


def check_question(question: str) -> str | None:
    text = question.strip()
    if not text:
        return "the question is empty"
    if len(text) > MAX_QUESTION_CHARS:
        return f"the question is longer than {MAX_QUESTION_CHARS} characters"
    for pattern, what in _OUT_OF_SCOPE:
        if pattern.search(text):
            return f"this asks for {what}; I only report what filings say"
    return None
