"""A data provider for the tests: a cell of `tokens` tokens and a scale, drawn from nothing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    name: str
    tokens: int
    scale: float = 1.0


def tokens(name: str, **params) -> Tokens:
    return Tokens(name, **params)


@dataclass(frozen=True)
class Named:
    """A cell named by its fields, as the central form's are."""

    tokens: int

    @property
    def name(self) -> str:
        return f"t{self.tokens}"


def named(name: str | None, **params) -> Named:
    cell = Named(**params)
    if name is not None and name != cell.name:
        raise ValueError(f"a cell's name is its fields' ({cell.name}); the record names {name}")
    return cell
