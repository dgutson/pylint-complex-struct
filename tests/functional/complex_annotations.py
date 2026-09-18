"""End-to-end fixture. Every offending line carries the symbol it must produce.

Kept to syntax that parses on Python 3.10, so the suite runs on every supported
version; the PEP 695 `type` statement is covered by the unit tests instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional, TypeAlias

Row: TypeAlias = dict[str, Any]
Table: TypeAlias = list[Row]
Blob: TypeAlias = dict[str, list[dict[str, Any]]]  # [complex-type-alias]


def load_phases() -> tuple[dict[str, tuple[int, int] | None], dict[int, str]]:  # [complex-type-annotation]
    """A map of spans, plus a reverse index."""
    return {}, {}


def load_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:  # [complex-type-annotation]
    """A list of records, plus a summary."""
    return [], {}


def build_index() -> tuple[dict[str, Any], list[dict[str, str]]]:  # [complex-type-annotation]
    """A mapping, plus a list of problems."""
    return {}, []


def already_factored() -> dict[str, Table]:
    """Composed from aliases: silent."""
    return {}


def quoted() -> "dict[str, list[tuple[int, int]]]":  # [complex-type-annotation]
    """Quoting is not an escape hatch."""
    return {}


def prose(widget: "the widget id") -> None:
    """Unparseable forward refs are prose, not types: silent."""


def load_pair() -> tuple[str, int]:  # [tuple-should-be-namedtuple]
    """A positional bag of unrelated values."""
    return "", 0


def coordinates() -> tuple[int, int]:
    """A fixed-size vector: silent."""
    return 0, 0


def rows() -> tuple[str, ...]:
    """A homogeneous sequence: silent."""
    return ()


def optional_payload() -> Optional[dict[str, int]]:
    """Nullability is a bit on a shape, not a level of nesting: silent."""
    return None


def transform(callback: Callable[[int, str], bool]) -> None:
    """The parameter bracket of Callable is syntax: silent."""


async def fetch() -> tuple[str, int]:  # [tuple-should-be-namedtuple]
    """The async spelling must behave like the sync one."""
    return "", 0


class Cache:
    """Attributes are checked too."""

    index: dict[str, list[tuple[int, int]]] = {}  # [complex-type-annotation]
    table: Table = []

    def __init__(self) -> None:
        self.pending: dict[str, list[tuple[int, int]]] = {}  # [complex-type-annotation]
