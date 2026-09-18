"""The metric, tested in isolation: astroid only, no linter."""

from __future__ import annotations

import astroid
import pytest
from astroid import nodes

from pylint_complex_struct.depth import (
    MAX_LEVEL,
    Policy,
    flatten_union,
    measure,
    namedtuple_candidate,
)
from pylint_complex_struct.names import ImportMap

#: The three annotations that prompted this checker, verbatim.
ARC = "tuple[dict[str, tuple[int, int] | None], dict[int, str]]"
ARCHIVE = "tuple[list[dict[str, Any]], dict[str, Any]]"
MANIFEST = "tuple[dict[str, Any], list[dict[str, str]]]"


def score(source: str, policy: Policy | None = None, imports: ImportMap | None = None) -> int:
    """Nesting depth of an annotation written as source."""
    node = astroid.extract_node(source)
    return measure(node, policy or Policy(), imports or ImportMap()).depth


def quoted(text: str) -> nodes.Const:
    """A quoted annotation, built where one really appears.

    A lone string at module level is a docstring, so `extract_node` cannot be
    used directly for these.
    """
    function = astroid.extract_node(f"def _f() -> {text!r}: ...")
    return function.returns


def score_node(node: nodes.NodeNG) -> int:
    """Nesting depth of an already-built annotation node."""
    return measure(node, Policy(), ImportMap()).depth


@pytest.mark.parametrize(
    "source, expected",
    [
        # leaves
        ("int", 1),
        ("Any", 1),
        ("None", 1),
        ("T", 1),
        ("list", 1),
        ("collections.abc.Sequence", 1),
        # the ordinary ladder
        ("list[int]", 2),
        ("dict[str, Any]", 2),
        ("dict[str, list[int]]", 3),
        ("dict[str, list[tuple[int, int]]]", 4),
        ("type[Foo]", 2),
        ("Awaitable[tuple[str, int]]", 3),
        # the real offenders
        (ARC, 4),
        (ARCHIVE, 4),
        (MANIFEST, 4),
        # unions
        ("int | None", 1),
        ("int | str", 2),
        ("int | str | None", 2),
        ("Optional[dict[str, list[int]]]", 3),
        # callables: the parameter bracket is syntax, the parameter types are not
        ("Callable[[int, str], bool]", 2),
        ("Callable[..., None]", 2),
        ("Callable[P, T]", 2),
        ("Callable[[dict[str, list[int]]], None]", 4),
        # values are not types
        ('Literal["a", "b", "c"]', 1),
        ("Literal[1, 2] | None", 1),
        # qualifiers describe usage, not shape
        ("Final[dict[str, list[int]]]", 3),
        ("ClassVar[dict[str, list[int]]]", 3),
        ("Required[list[int]]", 2),
        # ... is a marker, not a type
        ("tuple[int, ...]", 2),
        ("tuple[()]", 1),
    ],
)
def test_depth(source: str, expected: int) -> None:
    assert score(source) == expected


@pytest.mark.parametrize("source", [ARC, ARCHIVE, MANIFEST])
def test_real_examples_are_over_the_default_budget(source: str) -> None:
    assert score(source) > Policy().max_annotation_complexity


def test_pep604_union_is_not_free() -> None:
    """flake8-annotations-complexity scores this 1: it has no ast.BinOp case."""
    assert score("tuple[int, int] | None") == 2


def test_union_spellings_agree() -> None:
    """`A | B` and `Union[A, B]` must never drift apart."""
    assert score("int | str") == score("Union[int, str]")
    assert score("dict[str, int] | None") == score("Optional[dict[str, int]]")


def test_optional_can_be_counted() -> None:
    strict = Policy(count_optional=True)
    assert score("Optional[dict[str, list[int]]]", policy=strict) == 4
    assert score("dict[str, list[int]] | None", policy=strict) == 4


def test_union_counting_can_be_disabled() -> None:
    assert score("int | str", policy=Policy(count_union=False)) == 1


def test_callable_params_can_be_counted() -> None:
    assert score("Callable[[int, str], bool]", policy=Policy(count_callable_params=True)) == 3


def test_literal_members_are_never_parsed_as_forward_refs() -> None:
    assert score('Literal["dict[str, list[tuple[int, int]]]"]') == 1


def test_annotated_metadata_is_never_walked() -> None:
    assert score("Annotated[int, Field(max_length=10)]") == 1
    assert score('Annotated[dict[str, int], {"a": {"b": {"c": 1}}}]') == 2


def test_starred_and_unpack_agree() -> None:
    assert score("*tuple[int, str]") == score("Unpack[tuple[int, str]]") == 2


def test_quoting_is_not_an_escape_hatch() -> None:
    assert score_node(quoted("dict[str, list[tuple[int, int]]]")) == 4
    assert score_node(quoted("list['dict[str, list[int]]']")) == 4


@pytest.mark.parametrize("text", ["dict[str,", "some prose here", "Node", ""])
def test_unparseable_forward_refs_are_silent_leaves(text: str) -> None:
    assert score_node(quoted(text)) == 1


def test_module_alias_is_resolved() -> None:
    imports = ImportMap()
    imports.add_module("t", "typing")
    assert score("t.Dict[str, t.List[t.Tuple[int, int]]]", imports=imports) == 4


def test_from_import_alias_is_resolved() -> None:
    imports = ImportMap()
    imports.add_from("Opt", "typing", "Optional")
    assert score("Opt[dict[str, list[int]]]", imports=imports) == 3


def test_terms_are_counted() -> None:
    assert measure(astroid.extract_node("dict[str, Any]"), Policy(), ImportMap()).terms == 2
    wide = "tuple[int, str, float, bool, bytes, complex, range, slice]"
    assert measure(astroid.extract_node(wide), Policy(), ImportMap()).terms == 8


def _union_chain(size: int) -> nodes.BinOp:
    """Build `A0 | A1 | ... ` directly: astroid's own parser cannot manage this."""
    node: nodes.NodeNG = nodes.Name(
        name="A0", lineno=1, col_offset=0, parent=None, end_lineno=1, end_col_offset=2
    )
    for index in range(1, size):
        binop = nodes.BinOp(
            op="|", lineno=1, col_offset=0, parent=None, end_lineno=1, end_col_offset=2
        )
        right = nodes.Name(
            name=f"A{index}", lineno=1, col_offset=0, parent=binop, end_lineno=1, end_col_offset=2
        )
        binop.postinit(left=node, right=right)
        node = binop
    return node


def test_huge_union_does_not_recurse() -> None:
    """`|` parses left-deep; a recursive flatten would blow the stack inside pylint."""
    chain = _union_chain(1500)
    assert len(flatten_union(chain)) == 1500
    assert measure(chain, Policy(), ImportMap()).depth == 2


def test_pathological_union_is_capped_not_fatal() -> None:
    verdict = measure(_union_chain(5000), Policy(), ImportMap())
    assert verdict.truncated
    assert verdict.depth == MAX_LEVEL


def test_deep_nesting_is_capped_not_fatal() -> None:
    verdict = measure(
        astroid.extract_node("list[" * 200 + "int" + "]" * 200), Policy(), ImportMap()
    )
    assert verdict.truncated
    assert verdict.depth == MAX_LEVEL


@pytest.mark.parametrize(
    "source, expected_fields",
    [
        ("tuple[str, int]", 2),
        ("tuple[str, int, float]", 3),
        ("Optional[tuple[str, int]]", 2),
        ("tuple[str, int] | None", 2),
        ("Awaitable[tuple[str, int]]", 2),
        ("Coroutine[Any, Any, tuple[str, int]]", 2),
        ("Annotated[tuple[str, int], Field()]", 2),
    ],
)
def test_namedtuple_candidates(source: str, expected_fields: int) -> None:
    node = astroid.extract_node(source)
    result = namedtuple_candidate(node, ImportMap(), 2)
    assert result is not None
    assert result[0] == expected_fields


@pytest.mark.parametrize(
    "source",
    [
        "tuple[int, int]",  # a fixed-size vector, not a record
        "tuple[int, ...]",  # a homogeneous sequence
        "tuple[()]",
        "tuple[str]",  # below min_fields
        "list[tuple[str, int]]",  # the dict.items() shape: deliberately silent
        "Iterator[tuple[str, int]]",
        "dict[str, Any]",
        "tuple[int, Unpack[Ts]]",  # shape-polymorphic
        "tuple[int, *Ts]",
    ],
)
def test_not_namedtuple_candidates(source: str) -> None:
    node = astroid.extract_node(source)
    assert namedtuple_candidate(node, ImportMap(), 2) is None


def test_namedtuple_anchor_points_at_the_tuple() -> None:
    node = astroid.extract_node("Optional[tuple[str, int]]")
    result = namedtuple_candidate(node, ImportMap(), 2)
    assert result is not None
    assert result[1].as_string() == "tuple[str, int]"


def test_namedtuple_anchor_stays_on_the_string_for_forward_refs() -> None:
    """A parsed forward ref has synthetic positions, so the anchor must not move."""
    node = quoted("tuple[str, int]")
    result = namedtuple_candidate(node, ImportMap(), 2)
    assert result is not None
    assert result[0] == 2
    assert result[1] is node
