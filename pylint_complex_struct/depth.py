"""The annotation complexity metric.  Pure, and deliberately inference-free.

This module must not import pylint, so that the metric can be unit-tested with
nothing but ``astroid.extract_node``.

It must also never ask astroid what a name *refers to* -- no inference, no scope
lookups.  A name is always a leaf, whatever it expands to.  That is not a
shortcut, it is the whole point: it makes the rule actionable, because pulling a
subtree out into an alias mechanically brings the score back within budget and
re-linting the fixed file is clean.  A metric that expanded aliases would score
the fixed code exactly like the original, and there would be no legal way to
write the type at all.  ``tests/test_no_inference.py`` enforces this by grepping
the source of this module.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import NamedTuple

import astroid
from astroid import nodes

from .names import (
    ANNOTATED,
    CALLABLE,
    COROUTINE,
    LITERAL,
    NONE_NAMES,
    OPTIONAL,
    PEELABLE,
    TRANSPARENT,
    TUPLE_HEADS,
    UNION,
    UNPACK,
    ImportMap,
    resolve_head,
)

#: Nesting beyond this is over any sane budget, so we stop walking and say so
#: rather than risking a RecursionError inside pylint.
MAX_LEVEL = 32
#: Guard against machine-generated monsters.
MAX_NODES = 2000

_PARSEABLE = (
    nodes.Name,
    nodes.Attribute,
    nodes.Subscript,
    nodes.BinOp,
    nodes.Const,
    nodes.Tuple,
    nodes.List,
    nodes.Starred,
)


@dataclass(frozen=True)
class Policy:  # pylint: disable=too-many-instance-attributes  # a bundle of options
    """Resolved option values, built once per module.

    The field defaults are the options' defaults: the checker's option table reads
    them from here, so this is the only place a default is written.
    """

    max_annotation_complexity: int = 2
    max_alias_complexity: int = 2
    max_annotation_terms: int = 7
    count_optional: bool = False
    count_union: bool = True
    count_callable_params: bool = False
    min_namedtuple_fields: int = 2
    namedtuple_scopes: frozenset[str] = frozenset({"returns"})
    check_implicit_type_aliases: bool = False


@dataclass
class Verdict:
    """What one annotation scored."""

    depth: int
    terms: int
    #: True when the walk hit MAX_LEVEL or MAX_NODES, so `depth` is a floor.
    truncated: bool = False


@dataclass
class _Ctx:
    """Per-annotation walk state."""

    policy: Policy
    imports: ImportMap
    terms: int = 0
    seen: int = 0
    truncated: bool = False

    def budget_left(self) -> bool:
        """Count one node and report whether the walk may continue."""
        self.seen += 1
        return self.seen <= MAX_NODES

    def leaf(self) -> int:
        """Record a type term and score it as a leaf."""
        self.terms += 1
        return 1


@functools.lru_cache(maxsize=512)
def parse_forward_ref(text: str) -> nodes.NodeNG | None:
    """Parse a quoted annotation.  Quoting must not be an escape hatch.

    Failure is always silent and scores as a leaf: annotations like
    ``x: "the widget id"`` are prose, not types, and reporting on them would be
    worse than missing them.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 512 or "\n" in stripped:
        return None
    try:
        node = astroid.extract_node(stripped)
    except (astroid.AstroidSyntaxError, SyntaxError, ValueError, RecursionError, AttributeError):
        return None
    return node if isinstance(node, _PARSEABLE) else None


def slice_elements(slice_node: nodes.NodeNG | None) -> list[nodes.NodeNG]:
    """The arguments of a subscript.  ``dict[str, int]`` -> ``[str, int]``."""
    if slice_node is None:
        return []
    if isinstance(slice_node, nodes.Tuple):
        return list(slice_node.elts)
    return [slice_node]


def flatten_union(node: nodes.BinOp) -> list[nodes.NodeNG]:
    """Flatten ``a | b | c`` iteratively.

    Not an optimisation: ``|`` parses left-deep, so a generated thousand-member
    union would otherwise recurse a thousand frames and blow the stack inside
    pylint.
    """
    out: list[nodes.NodeNG] = []
    stack: list[nodes.NodeNG] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, nodes.BinOp) and current.op == "|":
            stack.append(current.left)
            stack.append(current.right)
        else:
            out.append(current)
    return out


def is_ellipsis(node: nodes.NodeNG | None) -> bool:
    """True for the `...` in `tuple[int, ...]` and `Callable[..., R]`."""
    return isinstance(node, nodes.Const) and node.value is Ellipsis


def is_none(node: nodes.NodeNG | None) -> bool:
    """True for every spelling of the None type."""
    if isinstance(node, nodes.Const) and node.value is None:
        return True
    if isinstance(node, nodes.Name) and node.name in NONE_NAMES:
        return True
    return isinstance(node, nodes.Attribute) and node.attrname == "NoneType"


def _max0(values: list[int]) -> int:
    return max(values) if values else 0


def _depth(  # pylint: disable=too-many-return-statements  # one return per node kind
    node: nodes.NodeNG | None, ctx: _Ctx, level: int
) -> int:
    if node is None:
        return 0
    if level >= MAX_LEVEL or not ctx.budget_left():
        ctx.truncated = True
        return MAX_LEVEL

    if isinstance(node, (nodes.Name, nodes.Attribute)):
        return ctx.leaf()

    if isinstance(node, nodes.Const):
        if node.value is Ellipsis:
            return 0
        if isinstance(node.value, str):
            parsed = parse_forward_ref(node.value)
            if parsed is None:
                return ctx.leaf()
            return _depth(parsed, ctx, level + 1)
        return ctx.leaf()

    if isinstance(node, nodes.Starred):
        return _depth(node.value, ctx, level + 1)

    if isinstance(node, (nodes.Tuple, nodes.List)):
        # A bare bracket: a subscript's argument list, or Callable's parameter
        # list.  Syntax, not nesting, so no +1.
        return _max0([_depth(elt, ctx, level + 1) for elt in node.elts])

    if isinstance(node, nodes.BinOp):
        if node.op == "|":
            return _union_depth(flatten_union(node), ctx, level)
        return ctx.leaf()

    if isinstance(node, nodes.Subscript):
        return _subscript_depth(node, ctx, level)

    return ctx.leaf()


def _union_depth(members: list[nodes.NodeNG], ctx: _Ctx, level: int) -> int:
    real = [m for m in members if not is_none(m)]
    had_none = len(real) != len(members)
    if not real:
        return ctx.leaf()
    inner = max(_depth(m, ctx, level + 1) for m in real)
    if len(real) == 1:
        # Optional-equivalent.  Nullability is a bit on a shape, not a shape.
        return inner + (1 if had_none and ctx.policy.count_optional else 0)
    return inner + (1 if ctx.policy.count_union else 0)


def _subscript_depth(  # pylint: disable=too-many-return-statements  # one per head kind
    node: nodes.Subscript, ctx: _Ctx, level: int
) -> int:
    head = resolve_head(node.value, ctx.imports)
    args = slice_elements(node.slice)

    if head in LITERAL:
        # Members are values, not types.  Recursing would also parse
        # Literal["dict[str, int]"] as a forward reference.
        return ctx.leaf()

    if head in ANNOTATED:
        # Metadata is arbitrary runtime objects; it has no type shape.
        return _depth(args[0], ctx, level + 1) if args else ctx.leaf()

    if head in TRANSPARENT:
        inner = _depth(args[0], ctx, level + 1) if args else ctx.leaf()
        if head in OPTIONAL and ctx.policy.count_optional:
            return inner + 1
        return inner

    if head in UNION:
        # Same code path as ``|`` so the two spellings can never disagree.
        return _union_depth(args, ctx, level)

    if head in CALLABLE:
        if len(args) >= 2:
            params, result = args[0], args[-1]
        else:
            params, result = None, (args[0] if args else None)
        param_depth = 0 if is_ellipsis(params) else _depth(params, ctx, level + 1)
        if ctx.policy.count_callable_params and param_depth:
            param_depth += 1
        return 1 + max(param_depth, _depth(result, ctx, level + 1), 1)

    return 1 + _max0([_depth(arg, ctx, level + 1) for arg in args])


def measure(node: nodes.NodeNG, policy: Policy, imports: ImportMap) -> Verdict:
    """Score one annotation."""
    ctx = _Ctx(policy=policy, imports=imports)
    value = _depth(node, ctx, 0)
    # Unwinding adds a level per frame, so a truncated walk can report more than
    # MAX_LEVEL. Clamp it and let the caller say "over" instead of a made-up number.
    return Verdict(
        depth=min(value, MAX_LEVEL) if ctx.truncated else value,
        terms=ctx.terms,
        truncated=ctx.truncated,
    )


class Peeled(NamedTuple):
    """A stripped annotation and the node a message about it should point at."""

    node: nodes.NodeNG | None
    anchor: nodes.NodeNG


class TupleShape(NamedTuple):
    """A fixed-size heterogeneous tuple found in an annotation."""

    fields: int
    anchor: nodes.NodeNG


def peel(node: nodes.NodeNG, imports: ImportMap) -> Peeled:
    """Strip wrappers that do not change "what shape is this really?".

    The anchor stays on the original node once we descend into a parsed forward
    reference, because those nodes carry positions from a synthetic module.
    """
    anchor = node
    synthetic = False
    for _ in range(MAX_LEVEL):
        if isinstance(node, nodes.Const) and isinstance(node.value, str):
            parsed = parse_forward_ref(node.value)
            if parsed is None:
                return Peeled(None, anchor)
            node, synthetic = parsed, True
            continue
        if isinstance(node, nodes.BinOp) and node.op == "|":
            real = [m for m in flatten_union(node) if not is_none(m)]
            if len(real) != 1:
                break
            node = real[0]
            if not synthetic:
                anchor = node
            continue
        if isinstance(node, nodes.Subscript):
            head = resolve_head(node.value, imports)
            args = slice_elements(node.slice)
            target = None
            if head in PEELABLE and args:
                target = args[0]
            elif head in COROUTINE and len(args) == 3:
                # Coroutine[Any, Any, X] must behave like `async def ... -> X`.
                target = args[2]
            if target is None:
                break
            node = target
            if not synthetic:
                anchor = node
            continue
        break
    return Peeled(node, anchor)


def namedtuple_candidate(  # pylint: disable=too-many-return-statements  # a filter chain
    node: nodes.NodeNG, imports: ImportMap, min_fields: int
) -> TupleShape | None:
    """The tuple shape, if this annotation is a heterogeneous fixed-size tuple."""
    peeled, anchor = peel(node, imports)
    if not isinstance(peeled, nodes.Subscript):
        return None
    if resolve_head(peeled.value, imports) not in TUPLE_HEADS:
        return None
    elements = slice_elements(peeled.slice)
    if len(elements) < max(min_fields, 1):
        return None
    for element in elements:
        if is_ellipsis(element) or isinstance(element, nodes.Starred):
            return None  # tuple[int, ...] is a sequence; tuple[*Ts] is polymorphic
        if isinstance(element, nodes.Subscript) and resolve_head(element.value, imports) in UNPACK:
            return None
    if len({element.as_string() for element in elements}) < 2:
        return None  # tuple[int, int] is a fixed-size vector, not a record
    return TupleShape(len(elements), anchor)
