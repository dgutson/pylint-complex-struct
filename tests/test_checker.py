"""The checker: which annotation sites are found, and what is emitted."""

from __future__ import annotations

import sys
import textwrap

import astroid
import pytest
from astroid import nodes
from pylint.interfaces import HIGH
from pylint.testutils import CheckerTestCase, MessageTest

from pylint_complex_struct.checker import ComplexStructChecker

ARC = "tuple[dict[str, tuple[int, int] | None], dict[int, str]]"
ARCHIVE = "tuple[list[dict[str, Any]], dict[str, Any]]"
MANIFEST = "tuple[dict[str, Any], list[dict[str, str]]]"
DEEP = "dict[str, list[tuple[int, int]]]"

needs_pep695 = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="the PEP 695 `type` statement needs Python 3.12"
)


def module(code: str) -> nodes.Module:
    """Parse a snippet the way pylint would see it."""
    return astroid.parse(textwrap.dedent(code))


class TestComplexStructChecker(CheckerTestCase):  # pylint: disable=too-many-public-methods
    """One test per behaviour, so a failure names the behaviour that broke."""

    CHECKER_CLASS = ComplexStructChecker

    # -- the contract ---------------------------------------------------------

    def test_correctly_factored_code_is_silent(self) -> None:
        """The no-inference contract.

        `Table` expands to something deep, but a name is a leaf. If this ever
        starts failing, someone has taught the metric to expand aliases and the
        rule has become impossible to satisfy.
        """
        node = module(
            """
            from typing import TypeAlias

            Row: TypeAlias = dict[str, int]
            Table: TypeAlias = list[Row]

            def load() -> dict[str, Table]:
                return {}

            def store(rows: Table) -> None:
                ...
            """
        )
        with self.assertNoMessages():
            self.walk(node)

    @needs_pep695
    def test_correctly_factored_pep695_code_is_silent(self) -> None:
        node = module(
            """
            type Row = dict[str, int]
            type Table = list[Row]

            def load() -> dict[str, Table]:
                return {}
            """
        )
        with self.assertNoMessages():
            self.walk(node)

    @pytest.mark.parametrize("annotation", [ARC, ARCHIVE, MANIFEST])
    def test_real_return_annotations_are_flagged_once(self, annotation: str) -> None:
        node = module(f"def load() -> {annotation}:\n    ...")
        function = node.body[0]
        with self.assertAddsMessages(
            MessageTest(
                msg_id="complex-type-annotation",
                node=function.returns,
                args=("the return value of 'load'", "nesting depth 4 > 2"),
                confidence=HIGH,
            ),
            ignore_position=True,
        ):
            self.walk(node)

    # -- annotation sites -----------------------------------------------------

    def test_parameter_annotation(self) -> None:
        node = module(f"def load(rows: {DEEP}) -> None:\n    ...")
        function = node.body[0]
        with self.assertAddsMessages(
            MessageTest(
                msg_id="complex-type-annotation",
                node=function.args.annotations[0],
                args=("parameter 'rows'", "nesting depth 4 > 2"),
                confidence=HIGH,
            ),
            ignore_position=True,
        ):
            self.walk(node)

    @pytest.mark.parametrize(
        "signature, subject",
        [
            (f"def f(*args: {DEEP}) -> None: ...", "parameter 'args'"),
            (f"def f(**kwargs: {DEEP}) -> None: ...", "parameter 'kwargs'"),
            (f"def f(a, /, b: {DEEP}) -> None: ...", "parameter 'b'"),
            (f"def f(*, only: {DEEP}) -> None: ...", "parameter 'only'"),
        ],
    )
    def test_every_argument_kind_is_covered(self, signature: str, subject: str) -> None:
        node = module(signature)
        messages = self._collect(node)
        assert [m.args[0] for m in messages] == [subject]

    def test_positional_only_annotation(self) -> None:
        node = module(f"def f(rows: {DEEP}, /) -> None: ...")
        assert [m.args[0] for m in self._collect(node)] == ["parameter 'rows'"]

    @pytest.mark.parametrize(
        "code, subject",
        [
            (f"class C:\n    cache: {DEEP} = {{}}", "attribute 'cache'"),
            (
                f"class C:\n    def __init__(self):\n        self.cache: {DEEP} = {{}}",
                "attribute 'self.cache'",
            ),
            (f"CACHE: {DEEP} = {{}}", "variable 'CACHE'"),
            (f"def f():\n    rows: {DEEP} = {{}}", "local variable 'rows'"),
        ],
    )
    def test_annassign_subjects(self, code: str, subject: str) -> None:
        assert [m.args[0] for m in self._collect(module(code))] == [subject]

    def test_two_bad_parameters_report_separately(self) -> None:
        node = module(f"def f(a: {DEEP}, b: {DEEP}) -> None: ...")
        messages = self._collect(node)
        assert [m.args[0] for m in messages] == ["parameter 'a'", "parameter 'b'"]
        assert messages[0].col_offset != messages[1].col_offset

    def test_return_none_is_fine(self) -> None:
        with self.assertNoMessages():
            self.walk(module("def f() -> None:\n    ...\n\ndef g():\n    ..."))

    def test_stub_files_are_skipped(self) -> None:
        node = module(f"def f() -> {ARC}: ...")
        node.file = "shapes.pyi"
        with self.assertNoMessages():
            self.walk(node)

    # -- aliases --------------------------------------------------------------

    @needs_pep695
    def test_alias_gets_the_laxer_budget(self) -> None:
        with self.assertNoMessages():
            self.walk(module("type Rows = list[dict[str, Any]]"))

    @needs_pep695
    def test_alias_over_its_own_budget(self) -> None:
        node = module("type Rows = list[dict[str, tuple[int, int]]]")
        alias = node.body[0]
        with self.assertAddsMessages(
            MessageTest(
                msg_id="complex-type-alias",
                node=alias.value,
                args=("'Rows'", "nesting depth 4 > 3"),
                confidence=HIGH,
            ),
            ignore_position=True,
        ):
            self.walk(node)

    def test_pep613_alias_marker_is_not_scored(self) -> None:
        with self.assertNoMessages():
            self.walk(
                module("from typing import TypeAlias\nRows: TypeAlias = list[dict[str, Any]]")
            )

    def test_pep613_alias_body_is_scored(self) -> None:
        node = module(
            "from typing import TypeAlias\nRows: TypeAlias = list[dict[str, list[tuple[int, int]]]]"
        )
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-alias"]

    def test_implicit_alias_is_off_by_default(self) -> None:
        with self.assertNoMessages():
            self.walk(module(f"Rows = dict[str, {DEEP}]"))

    def test_implicit_alias_when_enabled(self) -> None:
        self.linter.config.check_implicit_type_aliases = True
        node = module(f"Rows = dict[str, {DEEP}]")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-alias"]

    def test_screaming_case_constants_are_not_implicit_aliases(self) -> None:
        """A set union of constants is not a union type. Found by self-application."""
        self.linter.config.check_implicit_type_aliases = True
        self.linter.config.max_alias_complexity = 1
        with self.assertNoMessages():
            self.walk(module("PEELABLE = TRANSPARENT | ANNOTATED | AWAITABLE"))

    def test_camel_case_union_alias_still_fires(self) -> None:
        self.linter.config.check_implicit_type_aliases = True
        self.linter.config.max_alias_complexity = 1
        node = module("Payload = dict[str, int] | list[dict[str, int]]")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-alias"]

    def test_subscript_expressions_are_not_mistaken_for_aliases(self) -> None:
        self.linter.config.check_implicit_type_aliases = True
        with self.assertNoMessages():
            self.walk(module("cache = {}\nrows = cache['key']\nitem = rows[0][1]"))

    # -- NamedTuple heuristic -------------------------------------------------

    def test_heterogeneous_tuple_return(self) -> None:
        node = module("def load() -> tuple[str, int]:\n    ...")
        function = node.body[0]
        with self.assertAddsMessages(
            MessageTest(
                msg_id="tuple-should-be-namedtuple",
                node=function.returns,
                args=("The return value of 'load'", 2),
                confidence=HIGH,
            ),
            ignore_position=True,
        ):
            self.walk(node)

    def test_async_tuple_return(self) -> None:
        node = module("async def load() -> tuple[str, int]:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["tuple-should-be-namedtuple"]

    def test_coroutine_spelling_matches_async_def(self) -> None:
        """Only visible with a budget that does not trip the depth rule first."""
        self.linter.config.max_annotation_complexity = 3
        node = module("def load() -> Coroutine[Any, Any, tuple[str, int]]:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["tuple-should-be-namedtuple"]

    @pytest.mark.parametrize(
        "annotation",
        ["tuple[int, int]", "tuple[int, ...]", "tuple[()]", "tuple[str]", "dict[str, Any]"],
    )
    def test_quiet_tuple_returns(self, annotation: str) -> None:
        with self.assertNoMessages():
            self.walk(module(f"def load() -> {annotation}:\n    ..."))

    def test_parameters_are_out_of_scope_by_default(self) -> None:
        with self.assertNoMessages():
            self.walk(module("def load(pair: tuple[str, int]) -> None:\n    ..."))

    def test_parameters_can_be_brought_into_scope(self) -> None:
        self.linter.config.namedtuple_check_scope = ("returns", "params")
        node = module("def load(pair: tuple[str, int]) -> None:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["tuple-should-be-namedtuple"]

    def test_namedtuple_check_can_be_disabled(self) -> None:
        self.linter.config.namedtuple_check_scope = ()
        with self.assertNoMessages():
            self.walk(module("def load() -> tuple[str, int]:\n    ..."))

    def test_min_fields_is_respected(self) -> None:
        self.linter.config.min_namedtuple_fields = 3
        with self.assertNoMessages():
            self.walk(module("def load() -> tuple[str, int]:\n    ..."))

    # -- one message per site -------------------------------------------------

    def test_deep_tuple_return_reports_only_the_depth(self) -> None:
        node = module(f"def load() -> {MANIFEST}:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-annotation"]

    def test_items_shape_is_a_depth_violation_not_a_namedtuple_suggestion(self) -> None:
        node = module("def load() -> list[tuple[str, int]]:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-annotation"]

    def test_terms_budget(self) -> None:
        wide = "tuple[int, str, float, bool, bytes, complex, range, slice]"
        node = module(f"def load() -> {wide}:\n    ...")
        messages = self._collect(node)
        assert [(m.msg_id, m.args[1]) for m in messages] == [
            ("complex-type-annotation", "8 type terms > 7")
        ]

    # -- forward references ---------------------------------------------------

    def test_forward_ref_is_flagged_at_the_string(self) -> None:
        node = module(f'def load() -> "{DEEP}":\n    ...')
        const = node.body[0].returns
        with self.assertAddsMessages(
            MessageTest(
                msg_id="complex-type-annotation",
                node=const,
                line=const.lineno,
                col_offset=const.col_offset,
                end_line=const.end_lineno,
                end_col_offset=const.end_col_offset,
                args=("the return value of 'load'", "nesting depth 4 > 2"),
                confidence=HIGH,
            )
        ):
            self.walk(node)

    def test_prose_annotation_is_ignored(self) -> None:
        with self.assertNoMessages():
            self.walk(module('def load(widget: "the widget id") -> None:\n    ...'))

    # -- imports --------------------------------------------------------------

    def test_module_alias_imports_are_followed(self) -> None:
        node = module(
            "import typing as t\ndef load() -> t.Dict[str, t.List[t.Tuple[int, int]]]:\n    ..."
        )
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-annotation"]

    def test_from_import_alias_keeps_optional_transparent(self) -> None:
        node = module(
            "from typing import Optional as Opt\ndef load() -> Opt[dict[str, int]]:\n    ..."
        )
        with self.assertNoMessages():
            self.walk(node)

    # -- documented limits ----------------------------------------------------

    def test_user_defined_optional_is_a_known_false_negative(self) -> None:
        """The bare-name fallback assumes `typing`, so this scores 2 instead of 3.

        Accepted deliberately: re-exports and `if TYPE_CHECKING` blocks make the
        fallback worth far more than this costs.
        """
        node = module("class Optional:\n    ...\n\nx: Optional[dict[str, int]] = None")
        with self.assertNoMessages():
            self.walk(node)

    def test_an_unknown_wrapper_is_not_transparent(self) -> None:
        node = module("x: MyWrapper[dict[str, int]] = None")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-annotation"]

    # -- options --------------------------------------------------------------

    def test_budget_of_three_matches_the_flake8_default(self) -> None:
        self.linter.config.max_annotation_complexity = 3
        node = module(f"def load() -> {ARC}:\n    ...")
        assert [m.msg_id for m in self._collect(node)] == ["complex-type-annotation"]
        with self.assertNoMessages():
            self.walk(module("def load() -> list[dict[str, Any]]:\n    ..."))

    def test_unknown_scope_is_rejected(self) -> None:
        self.linter.config.namedtuple_check_scope = ("return",)
        with pytest.raises(ValueError, match="unknown namedtuple-check-scope"):
            self.checker.open()

    # -- helper ---------------------------------------------------------------

    def _collect(self, node: nodes.Module) -> list[MessageTest]:
        self.walk(node)
        return list(self.linter.release_messages())
