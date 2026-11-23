"""resolve_entity and resolve_concept (design.md section 8.3).

Both return ranked candidates, never a silent single pick: ambiguity is information the agent
and the confidence score need. Matching is exact (case- and punctuation-insensitive) first,
then fuzzy. Embedding-based concept matching arrives with the text path (M3).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

import psycopg

from verified_rag.store.db import QueryLimits
from verified_rag.store.templates import list_concept_aliases, list_entities

FUZZY_CUTOFF = 0.6
MAX_CANDIDATES = 5
_NON_WORD = re.compile(r"[^a-z0-9&]+")


def normalize_text(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: int
    name: str
    external_id: str
    matched: str
    score: float  # 1.0 = exact


@dataclass(frozen=True)
class ConceptCandidate:
    concept_id: int
    name: str
    description: str | None
    unit_kind: str
    matched: str
    score: float  # 1.0 = exact


def _score(query: str, label: str) -> float:
    if query == label:
        return 1.0
    ratio = SequenceMatcher(None, query, label).ratio()
    # Whole-word containment ("apple" in "apple inc") is a strong but not exact signal.
    if f" {query} " in f" {label} " or f" {label} " in f" {query} ":
        ratio = max(ratio, 0.9)
    return min(ratio, 0.99)


def _best(labels: Iterable[str], query: str) -> tuple[str, float]:
    best_label, best_score = "", 0.0
    for label in labels:
        score = _score(query, normalize_text(label))
        if score > best_score:
            best_label, best_score = label, score
    return best_label, best_score


def resolve_entity(
    conn: psycopg.Connection, pack_id: str, name: str, limits: QueryLimits
) -> list[EntityCandidate]:
    query = normalize_text(name)
    if not query:
        return []
    out: list[EntityCandidate] = []
    for row in list_entities(conn, pack_id, limits):
        labels = [row.name, row.external_id, *row.aliases]
        matched, score = _best(labels, query)
        if score >= FUZZY_CUTOFF:
            out.append(EntityCandidate(row.entity_id, row.name, row.external_id, matched, score))
    out.sort(key=lambda c: (-c.score, c.name))
    return out[:MAX_CANDIDATES]


def resolve_concept(
    conn: psycopg.Connection, pack_id: str, phrase: str, limits: QueryLimits
) -> list[ConceptCandidate]:
    query = normalize_text(phrase.replace("_", " "))
    if not query:
        return []
    best: dict[int, ConceptCandidate] = {}
    for row in list_concept_aliases(conn, pack_id, limits):
        label = row.alias.replace("_", " ") if row.source == "name" else row.alias
        score = _score(query, normalize_text(label))
        current = best.get(row.concept_id)
        if score >= FUZZY_CUTOFF and (current is None or score > current.score):
            best[row.concept_id] = ConceptCandidate(
                row.concept_id, row.concept_name, row.description, row.unit_kind, row.alias, score
            )
    out = sorted(best.values(), key=lambda c: (-c.score, c.name))
    return out[:MAX_CANDIDATES]


def unambiguous[T: (EntityCandidate, ConceptCandidate)](candidates: list[T]) -> T | None:
    """The single clear winner, or None if the caller must ask or refuse.

    Clear means: exactly one exact match, or no exact match and one fuzzy match at least
    0.15 ahead of the next.
    """
    if not candidates:
        return None
    exact = [c for c in candidates if c.score == 1.0]
    if exact:
        return exact[0] if len(exact) == 1 else None
    top = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    return top if top.score >= 0.85 and top.score - runner_up >= 0.15 else None
