"""The pylint checker: where annotations are collected and messages emitted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, NamedTuple

from astroid import nodes
from pylint.checkers import BaseChecker
from pylint.interfaces import HIGH

from .depth import MAX_LEVEL, Policy, Verdict, flatten_union, measure, namedtuple_candidate
from .names import GENERIC_ORIGINS, ImportMap, is_type_alias_annotation, resolve_head

if TYPE_CHECKING:
    from pylint.lint import PyLinter

VALID_SCOPES = frozenset({"returns", "params", "attributes", "locals", "aliases"})


class MessageKey(NamedTuple):
    """Identity of an emitted message, for the belt-and-braces dedup set."""

    symbol: str
    lineno: int
    col_offset: int


class AnnotationSite(NamedTuple):
    """One annotation found on a function signature."""

    annotation: nodes.NodeNG
    name: str


def _depth_reason(verdict: Verdict, budget: int) -> str:
    """Phrase the budget breach, without inventing a number for a truncated walk."""
    if verdict.truncated:
        return f"nesting depth over {MAX_LEVEL} > {budget}"
    return f"nesting depth {verdict.depth} > {budget}"


class ComplexStructChecker(BaseChecker):
    """Push nested inline annotations towards aliases, NamedTuples and TypedDicts."""

    name = "complex-struct"

    msgs = {
        "R9501": (
            "Type annotation of %s is too complex (%s); extract a type alias, "
            "a NamedTuple or a TypedDict",
            "complex-type-annotation",
            "Emitted when a type annotation nests type constructors more deeply, or "
            "spells out more type terms, than the configured budget. A reference to a "
            "type alias counts as a single term, so extracting an alias always fixes "
            "the message.",
        ),
        "R9502": (
            "Type alias %s is too complex (%s); compose it from smaller aliases",
            "complex-type-alias",
            "Emitted when the body of a type alias exceeds the (laxer) alias budget. "
            "Composing aliases is encouraged; hiding one unreadable structure behind a "
            "single name is not.",
        ),
        "R9503": (
            "%s is a heterogeneous %d-field tuple; return a NamedTuple (or a "
            "dataclass) so the fields have names",
            "tuple-should-be-namedtuple",
            "Emitted when a function returns a fixed-size tuple of differing types, "
            "forcing every call site to unpack it positionally.",
        ),
    }

    options = (
        (
            "max-annotation-complexity",
            {
                "default": 2,
                "type": "int",
                "metavar": "<int>",
                "help": "Maximum nesting depth of a type annotation. 'int' is 1, "
                "'list[int]' is 2, 'dict[str, list[int]]' is 3. A reference to a type "
                "alias counts as 1, whatever the alias expands to.",
            },
        ),
        (
            "max-alias-complexity",
            {
                "default": 3,
                "type": "int",
                "metavar": "<int>",
                "help": "Maximum nesting depth allowed in the body of a type alias "
                "('type X = ...', 'X: TypeAlias = ...'). Laxer than "
                "max-annotation-complexity, because absorbing structure is what an "
                "alias is for.",
            },
        ),
        (
            "max-annotation-terms",
            {
                "default": 7,
                "type": "int",
                "metavar": "<int>",
                "help": "Maximum number of type terms (leaves) in one annotation; a "
                "Literal[...] counts as one term. Set to 0 to disable.",
            },
        ),
        (
            "count-optional-as-nesting",
            {
                "default": False,
                "type": "yn",
                "metavar": "<y or n>",
                "help": "Count 'Optional[X]' and 'X | None' as a level of nesting.",
            },
        ),
        (
            "count-union-as-nesting",
            {
                "default": True,
                "type": "yn",
                "metavar": "<y or n>",
                "help": "Count a union of two or more non-None members as a level of "
                "nesting. Applies equally to 'A | B' and 'Union[A, B]'.",
            },
        ),
        (
            "count-callable-params-as-nesting",
            {
                "default": False,
                "type": "yn",
                "metavar": "<y or n>",
                "help": "Count the parameter-list bracket of 'Callable[[A, B], R]' as a "
                "level of nesting. The parameter types themselves are always counted.",
            },
        ),
        (
            "namedtuple-check-scope",
            {
                "default": ("returns",),
                "type": "csv",
                "metavar": "<scopes>",
                "help": "Where to suggest a NamedTuple for heterogeneous fixed-size "
                "tuples: any of returns, params, attributes, locals, aliases. Empty "
                "disables the check.",
            },
        ),
        (
            "min-namedtuple-fields",
            {
                "default": 2,
                "type": "int",
                "metavar": "<int>",
                "help": "Minimum number of elements a heterogeneous tuple must have "
                "before a NamedTuple is suggested.",
            },
        ),
        (
            "check-implicit-type-aliases",
            {
                "default": False,
                "type": "yn",
                "metavar": "<y or n>",
                "help": "Also treat unannotated module- or class-level assignments whose "
                "value is a generic subscription ('Rows = dict[str, list[int]]') as type "
                "aliases.",
            },
        ),
    )

    def __init__(self, linter: PyLinter | None = None) -> None:
        super().__init__(linter)
        self._imports = ImportMap()
        self._policy: Policy | None = None
        self._reported: set[MessageKey] = set()
        self._skip_module = False

    # -- lifecycle ------------------------------------------------------------

    def open(self) -> None:
        """Validate the options once, so a typo is not silently ignored."""
        unknown = sorted(set(self.linter.config.namedtuple_check_scope) - VALID_SCOPES - {""})
        if unknown:
            raise ValueError(
                f"{self.name}: unknown namedtuple-check-scope value(s) {unknown}; "
                f"valid values are {sorted(VALID_SCOPES)}"
            )

    def visit_module(self, node: nodes.Module) -> None:
        """Reset per-module state and resolve the options."""
        self._imports = ImportMap()
        self._reported = set()
        self._policy = self._build_policy()
        # Stubs describe APIs the author often cannot refactor.
        self._skip_module = bool(node.file and node.file.endswith(".pyi"))

    def leave_module(self, _node: nodes.Module) -> None:
        """Drop per-module state."""
        self._imports = ImportMap()
        self._reported = set()
        self._policy = None
        self._skip_module = False

    def visit_import(self, node: nodes.Import) -> None:
        """Record `import typing as t`."""
        for name, alias in node.names:
            self._imports.add_module(alias or name, name)

    def visit_importfrom(self, node: nodes.ImportFrom) -> None:
        """Record `from typing import Optional as Opt`."""
        for name, alias in node.names:
            self._imports.add_from(alias or name, node.modname, name)

    # -- annotation sites -----------------------------------------------------

    def visit_functiondef(self, node: nodes.FunctionDef) -> None:
        """Check every parameter and the return annotation."""
        if self._skip_module:
            return
        for annotation, arg_name in self._arg_annotations(node.args):
            self._check(annotation, f"parameter '{arg_name}'", "params")
        if node.returns is not None:
            self._check(node.returns, f"the return value of '{node.name}'", "returns")

    visit_asyncfunctiondef = visit_functiondef

    def visit_annassign(self, node: nodes.AnnAssign) -> None:
        """Check an annotated assignment, or a PEP 613 alias body."""
        if self._skip_module:
            return
        if is_type_alias_annotation(node.annotation, self._imports):
            # PEP 613. The annotation is just the marker; the alias body is the value.
            if node.value is not None:
                self._check_alias(node.value, self._target_name(node.target))
            return
        subject, scope = self._annassign_subject(node)
        self._check(node.annotation, subject, scope)

    def visit_typealias(self, node: nodes.TypeAlias) -> None:
        """Check a PEP 695 `type X = ...` body."""
        if self._skip_module:
            return
        self._check_alias(node.value, node.name.name)

    def visit_assign(self, node: nodes.Assign) -> None:
        """Check an unannotated assignment that looks like a type alias."""
        policy = self._current_policy()
        if self._skip_module or not policy.check_implicit_type_aliases:
            return
        if len(node.targets) != 1:
            return
        target = node.targets[0]
        if not isinstance(target, nodes.AssignName):
            return
        # SCREAMING_CASE is a constant (PEP 8), not a type alias: `PEELABLE = TRANSPARENT
        # | ANNOTATED` is a frozenset union, not a union type. Found by running this
        # checker over its own source.
        if target.name.isupper():
            return
        if not isinstance(node.scope(), (nodes.Module, nodes.ClassDef)):
            return
        if not self._looks_like_alias(node.value):
            return
        self._check_alias(node.value, target.name)

    # -- checks ---------------------------------------------------------------

    def _check(self, annotation: nodes.NodeNG, subject: str, scope: str) -> None:
        policy = self._current_policy()
        verdict = measure(annotation, policy, self._imports)
        if verdict.depth > policy.max_annotation_complexity:
            reason = _depth_reason(verdict, policy.max_annotation_complexity)
            self._report("complex-type-annotation", annotation, (subject, reason))
            return
        if policy.max_annotation_terms and verdict.terms > policy.max_annotation_terms:
            reason = f"{verdict.terms} type terms > {policy.max_annotation_terms}"
            self._report("complex-type-annotation", annotation, (subject, reason))
            return
        self._check_heuristics(annotation, subject, scope)

    def _check_alias(self, body: nodes.NodeNG, name: str) -> None:
        policy = self._current_policy()
        verdict = measure(body, policy, self._imports)
        if verdict.depth > policy.max_alias_complexity:
            reason = _depth_reason(verdict, policy.max_alias_complexity)
            self._report("complex-type-alias", body, (f"'{name}'", reason))
            return
        if policy.max_annotation_terms and verdict.terms > policy.max_annotation_terms:
            reason = f"{verdict.terms} type terms > {policy.max_annotation_terms}"
            self._report("complex-type-alias", body, (f"'{name}'", reason))
            return
        self._check_heuristics(body, f"type alias '{name}'", "aliases")

    def _check_heuristics(self, annotation: nodes.NodeNG, subject: str, scope: str) -> None:
        policy = self._current_policy()
        if scope not in policy.namedtuple_scopes:
            return
        candidate = namedtuple_candidate(annotation, self._imports, policy.min_namedtuple_fields)
        if candidate is None:
            return
        self._report(
            "tuple-should-be-namedtuple",
            candidate.anchor,
            (subject[:1].upper() + subject[1:], candidate.fields),
        )

    def _report(self, symbol: str, node: nodes.NodeNG, args: tuple[object, ...]) -> None:
        key = MessageKey(symbol, node.lineno or 0, node.col_offset or 0)
        if key in self._reported:
            return
        self._reported.add(key)
        self.add_message(symbol, node=node, args=args, confidence=HIGH)

    # -- helpers --------------------------------------------------------------

    def _current_policy(self) -> Policy:
        # Normally built in visit_module; rebuilt lazily so that calling a single
        # visitor in isolation (as the unit tests do) still works.
        if self._policy is None:
            self._policy = self._build_policy()
        return self._policy

    def _build_policy(self) -> Policy:
        config = self.linter.config
        scopes = frozenset(s for s in config.namedtuple_check_scope if s)
        return Policy(
            max_annotation_complexity=config.max_annotation_complexity,
            max_alias_complexity=config.max_alias_complexity,
            max_annotation_terms=config.max_annotation_terms,
            count_optional=config.count_optional_as_nesting,
            count_union=config.count_union_as_nesting,
            count_callable_params=config.count_callable_params_as_nesting,
            min_namedtuple_fields=config.min_namedtuple_fields,
            namedtuple_scopes=scopes,
            check_implicit_type_aliases=config.check_implicit_type_aliases,
        )

    @staticmethod
    def _arg_annotations(args: nodes.Arguments) -> Iterator[AnnotationSite]:
        groups = (
            (args.posonlyargs, args.posonlyargs_annotations),
            (args.args, args.annotations),
            (args.kwonlyargs, args.kwonlyargs_annotations),
        )
        for arg_nodes, annotations in groups:
            for arg, annotation in zip(arg_nodes or [], annotations or []):
                if annotation is not None:
                    yield AnnotationSite(annotation, arg.name)
        if args.varargannotation is not None:
            yield AnnotationSite(args.varargannotation, args.vararg)
        if args.kwargannotation is not None:
            yield AnnotationSite(args.kwargannotation, args.kwarg)

    @staticmethod
    def _target_name(target: nodes.NodeNG) -> str:
        return getattr(target, "name", None) or target.as_string()

    def _annassign_subject(self, node: nodes.AnnAssign) -> tuple[str, str]:
        target = node.target
        if isinstance(target, nodes.AssignAttr):
            return f"attribute '{target.as_string()}'", "attributes"
        name = self._target_name(target)
        scope = node.scope()
        if isinstance(scope, nodes.ClassDef):
            return f"attribute '{name}'", "attributes"
        if isinstance(scope, nodes.Module):
            return f"variable '{name}'", "locals"
        return f"local variable '{name}'", "locals"

    def _looks_like_alias(self, value: nodes.NodeNG | None) -> bool:
        """Tell `Rows = dict[str, int]` from `rows = cache["key"]`, syntactically."""
        if isinstance(value, nodes.Subscript):
            return resolve_head(value.value, self._imports) in GENERIC_ORIGINS
        if isinstance(value, nodes.BinOp) and value.op == "|":
            members = flatten_union(value)
            return all(
                isinstance(m, (nodes.Name, nodes.Attribute, nodes.Subscript))
                or (isinstance(m, nodes.Const) and m.value is None)
                for m in members
            )
        return False
