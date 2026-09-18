"""Syntactic resolution of typing construct names.

Everything here is deliberately syntactic: the head of a subscript is resolved
by looking at what was written and at the module's import statements, never by
asking astroid what a name refers to.  See :mod:`pylint_complex_struct.depth`
for why that matters.

The one accepted cost is the bare-name fallback: a bare ``Optional`` is treated
as ``typing.Optional`` even with no visible import, because re-exports and
``if TYPE_CHECKING`` blocks are common.  A user-defined class literally named
``Optional`` is therefore mis-classified.  That is documented and tested.
"""

from __future__ import annotations

from astroid import nodes

# --- canonical category sets -------------------------------------------------

LITERAL = frozenset({"typing.Literal"})
ANNOTATED = frozenset({"typing.Annotated"})
UNION = frozenset({"typing.Union"})
OPTIONAL = frozenset({"typing.Optional"})
CALLABLE = frozenset({"typing.Callable"})
UNPACK = frozenset({"typing.Unpack"})
AWAITABLE = frozenset({"typing.Awaitable"})
COROUTINE = frozenset({"typing.Coroutine"})
TUPLE_HEADS = frozenset({"builtins.tuple"})
TYPE_ALIAS_NAMES = frozenset({"typing.TypeAlias"})

#: Wrappers that describe how a name is *used* rather than what shape it holds.
#: ``depth(Wrapper[T]) == depth(T)``.
TRANSPARENT = frozenset(
    {
        "typing.Optional",
        "typing.Final",
        "typing.ClassVar",
        "typing.Required",
        "typing.NotRequired",
        "typing.ReadOnly",
        "typing.Unpack",
        "typing.TypeGuard",
        "typing.TypeIs",
        "dataclasses.InitVar",
    }
)

#: Wrappers peeled off before asking "is this really a record?".
#: ``Coroutine`` is handled separately because the payload is the third argument.
PEELABLE = TRANSPARENT | ANNOTATED | AWAITABLE

#: Heads that make an unannotated assignment look like a type alias.
GENERIC_ORIGINS = frozenset(
    {
        "builtins.dict",
        "builtins.list",
        "builtins.tuple",
        "builtins.set",
        "builtins.frozenset",
        "builtins.type",
        "collections.abc.Mapping",
        "collections.abc.MutableMapping",
        "collections.abc.Sequence",
        "collections.abc.Iterable",
        "collections.abc.Iterator",
        "collections.defaultdict",
        "collections.deque",
    }
    | TRANSPARENT
    | ANNOTATED
    | UNION
    | CALLABLE
    | LITERAL
    | AWAITABLE
    | COROUTINE
)

_CANONICAL = {
    "typing.Dict": "builtins.dict",
    "typing.List": "builtins.list",
    "typing.Tuple": "builtins.tuple",
    "typing.Set": "builtins.set",
    "typing.FrozenSet": "builtins.frozenset",
    "typing.Type": "builtins.type",
    "typing.DefaultDict": "collections.defaultdict",
    "typing.Deque": "collections.deque",
    "typing.Mapping": "collections.abc.Mapping",
    "typing.MutableMapping": "collections.abc.MutableMapping",
    "typing.Sequence": "collections.abc.Sequence",
    "typing.Iterable": "collections.abc.Iterable",
    "typing.Iterator": "collections.abc.Iterator",
    "collections.abc.Callable": "typing.Callable",
    "collections.abc.Awaitable": "typing.Awaitable",
    "collections.abc.Coroutine": "typing.Coroutine",
}

_TYPING_BARE = (
    "Optional Union Literal Annotated Callable Final ClassVar Required NotRequired "
    "ReadOnly Unpack TypeGuard TypeIs Awaitable Coroutine TypeAlias Dict List Tuple "
    "Set FrozenSet Type DefaultDict Deque Mapping MutableMapping Sequence Iterable "
    "Iterator"
).split()

_BARE_FALLBACK: dict[str, str] = {
    name: _CANONICAL.get(f"typing.{name}", f"typing.{name}") for name in _TYPING_BARE
}
_BARE_FALLBACK.update(
    {name: f"builtins.{name}" for name in ("dict", "list", "tuple", "set", "frozenset", "type")}
)
_BARE_FALLBACK["InitVar"] = "dataclasses.InitVar"
# collections.abc spellings that are commonly imported bare
_BARE_FALLBACK.update(
    {
        name: f"collections.abc.{name}"
        for name in ("Mapping", "MutableMapping", "Sequence", "Iterable", "Iterator")
    }
)

NONE_NAMES = frozenset({"None", "NoneType"})


class ImportMap:
    """Per-module map from locally visible names to canonical dotted names."""

    __slots__ = ("_modules", "_names")

    def __init__(self) -> None:
        self._modules: dict[str, str] = {}
        self._names: dict[str, str] = {}

    def add_module(self, local: str, dotted: str) -> None:
        """Record ``import typing as t`` as ``t -> typing``."""
        self._modules[local] = dotted

    def add_from(self, local: str, modname: str, name: str) -> None:
        """Record ``from typing import Optional as Opt`` as ``Opt -> typing.Optional``."""
        self._names[local] = f"{modname}.{name}" if modname else name

    def resolve_prefix(self, local: str) -> str | None:
        """The module a dotted head starts from, e.g. `t` in `t.Dict`."""
        return self._modules.get(local) or self._names.get(local)

    def resolve_name(self, local: str) -> str | None:
        """The canonical name a bare identifier was imported as."""
        return self._names.get(local)


def dotted_name(node: nodes.NodeNG | None) -> str | None:
    """``t.Dict`` -> ``"t.Dict"``; anything that is not a dotted name -> ``None``."""
    if isinstance(node, nodes.Name):
        return node.name
    if isinstance(node, nodes.Attribute):
        prefix = dotted_name(node.expr)
        return f"{prefix}.{node.attrname}" if prefix else None
    return None


def canonical(dotted: str) -> str:
    """Fold spelling variants together: `typing_extensions.X` and `typing.Dict`."""
    if dotted.startswith("typing_extensions."):
        dotted = "typing." + dotted.split(".", 1)[1]
    return _CANONICAL.get(dotted, dotted)


def resolve_head(node: nodes.NodeNG | None, imports: ImportMap) -> str | None:
    """Canonical dotted name for the head of a subscript, or ``None`` if unknown."""
    dotted = dotted_name(node)
    if dotted is None:
        return None
    parts = dotted.split(".")
    if len(parts) == 1:
        qualified = imports.resolve_name(parts[0])
        if qualified is not None:
            return canonical(qualified)
        return _BARE_FALLBACK.get(parts[0])
    prefix = imports.resolve_prefix(parts[0])
    if prefix is not None:
        parts = prefix.split(".") + parts[1:]
    return canonical(".".join(parts))


def is_type_alias_annotation(node: nodes.NodeNG | None, imports: ImportMap) -> bool:
    """True for the ``TypeAlias`` in ``Rows: TypeAlias = ...`` (PEP 613)."""
    return resolve_head(node, imports) in TYPE_ALIAS_NAMES
