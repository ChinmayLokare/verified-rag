"""What a domain pack provides, and what its connector returns (design.md section 9).

The core knows these shapes and nothing about any particular domain. A pack folder holds
``pack.yaml``, ``concepts.yaml``, ``units.yaml``, and ``connector.py`` with a
``create_connector(ctx)`` function.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

PACK_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- pack specification (YAML) ---------------------------------------------------------------


class EntityConfig(_Frozen):
    ref: str = Field(min_length=1, description="Pack-specific reference, e.g. a ticker")
    aliases: tuple[str, ...] = ()


class UnitSpec(_Frozen):
    kind: str


class ConceptSpec(_Frozen):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    unit_kind: str
    tags: tuple[str, ...] = Field(description="Source tags in priority order, first wins")
    aliases: tuple[str, ...] = ()


class PackSpec(_Frozen):
    id: str = Field(pattern=PACK_ID.pattern)
    display_name: str
    parser_version: str
    document_types: tuple[str, ...]
    entities: tuple[EntityConfig, ...]
    concepts: tuple[ConceptSpec, ...]
    units: dict[str, UnitSpec]

    @model_validator(mode="after")
    def _consistent(self) -> "PackSpec":
        names = [c.name for c in self.concepts]
        if len(names) != len(set(names)):
            raise ValueError("concept names must be unique")
        seen: dict[str, str] = {}
        for concept in self.concepts:
            for tag in concept.tags:
                if tag in seen:
                    raise ValueError(f"tag {tag} maps to both {seen[tag]} and {concept.name}")
                seen[tag] = concept.name
        kinds = {u.kind for u in self.units.values()}
        for concept in self.concepts:
            if concept.unit_kind not in kinds:
                raise ValueError(f"{concept.name}: unit kind {concept.unit_kind} not in units")
        return self


# --- connector output ------------------------------------------------------------------------


class EntityRecord(_Frozen):
    external_id: str
    name: str
    aliases: tuple[str, ...] = ()
    attributes: dict[str, str | int | None] = Field(default_factory=dict)
    fiscal_year_end: str | None = Field(default=None, description="MMDD; None = calendar year")


class DocumentRecord(_Frozen):
    external_id: str
    doc_type: str
    period_end: date | None
    filed_at: datetime | None
    source_uri: str


class SourceFact(_Frozen):
    document_external_id: str
    tag: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    # What the source says the filing's fiscal period is. Used only as a cross-check.
    reported_fiscal_year: int | None = None
    reported_fiscal_period: str | None = None


class EntityBatch(_Frozen):
    entity: EntityRecord
    documents: tuple[DocumentRecord, ...]
    facts: tuple[SourceFact, ...]


# --- connector protocol ----------------------------------------------------------------------


@dataclass(frozen=True)
class PackContext:
    raw_dir: Path  # this pack's folder in the raw document store
    document_types: frozenset[str]  # only documents of these types are wanted
    offline: bool = False  # use only what is already in raw_dir


class Connector(Protocol):
    def fetch_entity(self, entity: EntityConfig, wanted_tags: frozenset[str]) -> EntityBatch:
        """Fetch one entity's documents and the facts carrying any of ``wanted_tags``."""
        ...


class ConnectorFactory(Protocol):
    def __call__(self, ctx: PackContext) -> Connector: ...


@dataclass(frozen=True)
class Pack:
    spec: PackSpec
    root: Path
    create_connector: ConnectorFactory
    prompt: str = ""  # domain notes for the agent, from prompt.md
