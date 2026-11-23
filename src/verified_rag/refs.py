"""Evidence references: the IDs the model and the verifier pass around (``f_12``, ``calc_3``)."""

import re
from dataclasses import dataclass
from typing import Literal

RefKind = Literal["fact", "calc"]

_PREFIX: dict[RefKind, str] = {"fact": "f", "calc": "calc"}
_PATTERN = re.compile(r"^(?P<prefix>f|calc)_(?P<id>[1-9]\d{0,17})$")


class RefError(ValueError):
    pass


@dataclass(frozen=True)
class Ref:
    kind: RefKind
    id: int

    def __str__(self) -> str:
        return f"{_PREFIX[self.kind]}_{self.id}"


def fact_ref(fact_id: int) -> str:
    return str(Ref("fact", fact_id))


def calc_ref(calc_id: int) -> str:
    return str(Ref("calc", calc_id))


def parse_ref(text: str) -> Ref:
    match = _PATTERN.match(text.strip())
    if match is None:
        raise RefError(f"not a fact or calculation reference: {text!r}")
    kind: RefKind = "fact" if match["prefix"] == "f" else "calc"
    return Ref(kind, int(match["id"]))
