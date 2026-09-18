# pylint-complex-struct

A pylint checker for over-nested type annotations. It exists because things like
this keep landing in real code, from humans and LLMs alike:

```python
def load_phases() -> tuple[dict[str, tuple[int, int] | None], dict[int, str]]: ...
def load_records() -> tuple[list[dict[str, Any]], dict[str, Any]]: ...
def build_index() -> tuple[dict[str, Any], list[dict[str, str]]]: ...
```

**The rule: name the structure.** Use a type alias, a `NamedTuple` or a
`TypedDict`. Composing aliases is free and encouraged.

Nothing in pylint 4 checks this (`pylint.extensions.typing` only covers
redundant and deprecated typing constructs), and ruff has no equivalent rule.

## Install and use

```bash
pip install -e .
pylint --load-plugins=pylint_complex_struct yourpackage
```

Or in `pyproject.toml`:

```toml
[tool.pylint.main]
load-plugins = ["pylint_complex_struct"]

[tool.pylint."complex-struct"]
max-annotation-complexity = 2
```

## Messages

| ID | Symbol | Fires on |
|---|---|---|
| `R9501` | `complex-type-annotation` | an annotation deeper than `max-annotation-complexity`, or with more terms than `max-annotation-terms` |
| `R9502` | `complex-type-alias` | the *body* of a type alias, over the laxer `max-alias-complexity` |
| `R9503` | `tuple-should-be-namedtuple` | a return annotation that is a heterogeneous fixed-size tuple |

At most one message is emitted per annotation site: the depth rule wins over the
`NamedTuple` suggestion, and an alias body only ever produces `R9502`.

## The metric

Depth counts subscript levels, and **a name is always a leaf**:

```
int                          1
dict[str, Any]               2      <- legal by default
list[dict[str, Any]]         3      <- flagged
type Row = dict[str, Any]
type Table = list[Row]
dict[str, Table]             2      <- legal: extracting the alias fixed it
```

That last line is the whole design. The metric is purely syntactic and never
asks astroid what a name refers to, so pulling a subtree out into an alias
mechanically brings the score back within budget. A metric that expanded
aliases would score the fixed code exactly like the original — the checker
could never be satisfied, and there would be no legal way to write the type at
all. `tests/test_no_inference.py` enforces this by grepping the source.

It also means the result does not depend on whether third-party packages happen
to be installed, so CI and your laptop agree.

### What does not count as nesting

| Construct | Treatment | Why |
|---|---|---|
| `X \| None`, `Optional[X]` | transparent | nullability is a bit on a shape, not a shape to decompose. Aliasing it away hides optionality at the call site, which is worse code. Configurable. |
| `A \| B`, `Union[A, B]` | one level | a real branch the reader must hold. Both spellings share one code path. |
| `Callable[[A, B], R]` | the param bracket is free; param *types* count | the bracket is mandatory syntax, not chosen nesting. Configurable. |
| `Literal["a", "b"]` | leaf, contents never walked | members are values, not types; there is nothing to extract. |
| `Annotated[T, meta]` | transparent, metadata never walked | metadata is arbitrary runtime objects. Every Pydantic/FastAPI codebase would otherwise light up. |
| `Final`, `ClassVar`, `Required`, `NotRequired`, `ReadOnly`, `Unpack`, `TypeGuard`, `TypeIs`, `InitVar` | transparent | they describe how a name is used, not what shape it holds. |
| `*tuple[int, str]` | transparent | must score the same as `Unpack[tuple[int, str]]`. |
| `...` in `tuple[int, ...]` | contributes nothing | a marker, not a type. |
| class bases | never visited | an alias cannot cleanly replace a base. |

Quoting is **not** an escape hatch: `-> "dict[str, list[tuple[int, int]]]"` scores
the same as the unquoted form. A forward reference that will not parse (`x: "the
widget id"`) scores as a leaf and is silently ignored.

### Aliases

Alias bodies get their own, laxer budget, because absorbing structure is what an
alias is for — but hiding one unreadable structure behind a name has only moved
the problem:

```python
type Row   = dict[str, Any]                       # fine
type Table = list[Row]                            # composing is free
type Blob  = dict[str, list[dict[str, Any]]]      # R9502: depth 4 > 3
```

`type X = ...` (PEP 695) and `X: TypeAlias = ...` (PEP 613) are both recognised.
Unannotated `Rows = dict[str, int]` is opt-in via `check-implicit-type-aliases`,
because `rows = cache["key"]` is also an assignment whose value is a subscript
and there is no sound syntactic way to tell them apart in general. A
SCREAMING_CASE target is skipped: by PEP 8 that is a constant, so
`PEELABLE = TRANSPARENT | ANNOTATED` is a frozenset union rather than a union type.

### The NamedTuple suggestion

`R9503` fires on a returned fixed-size tuple whose elements differ, because that
forces every call site to unpack positionally and re-invent names for the fields:

```python
def load() -> tuple[Config, int]: ...     # R9503
def coords() -> tuple[int, int]: ...      # silent: a fixed-size vector
def rows() -> tuple[str, ...]: ...        # silent: a homogeneous sequence
def items() -> list[tuple[str, int]]: ... # silent as a suggestion: the dict.items() shape
```

Returns only, by default. A parameter typed `tuple[str, int]` is usually
pass-through and the caller already has the values named.

A `dict[str, Any]` → `TypedDict` rule was considered and deliberately left out
for now: it is the noisiest of the family, since plenty of codebases use
`dict[str, Any]` legitimately at JSON boundaries.

## Options

All under `[tool.pylint."complex-struct"]`.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `max-annotation-complexity` | int | `2` | Max nesting depth of an annotation. |
| `max-alias-complexity` | int | `3` | Max nesting depth of an alias body. |
| `max-annotation-terms` | int | `7` | Max number of type terms in one annotation; `0` disables. |
| `count-optional-as-nesting` | yn | `n` | Count `Optional[X]` / `X \| None` as a level. |
| `count-union-as-nesting` | yn | `y` | Count a 2+ member union as a level. |
| `count-callable-params-as-nesting` | yn | `n` | Count `Callable`'s parameter bracket. |
| `namedtuple-check-scope` | csv | `returns` | Any of `returns,params,attributes,locals,aliases`; empty disables `R9503`. |
| `min-namedtuple-fields` | int | `2` | Minimum elements before suggesting a NamedTuple. |
| `check-implicit-type-aliases` | yn | `n` | Treat `Rows = dict[str, int]` as an alias. |

**Tuning.** The default budget of 2 is stricter than the flake8 equivalent's 3.
It catches all three examples above, but it also flags `dict[str, list[int]]` and
`list[tuple[str, int]]`. If the first run on an existing codebase is too loud,
set `max-annotation-complexity = 3`; that still catches the first two examples.

## Why pylint, and not flake8 or ruff

**flake8** would be marginally simpler to bootstrap and worse to live with. A
flake8 plugin is a class taking `(tree, filename)` and a `run()` yielding
`(line, col, "XXX001 text", type(self))`, registered through a `flake8.extension`
entry point — perhaps 30 lines less scaffolding than pylint's `BaseChecker`. But
roughly 70% of this project is the depth function, which would be identical, and
since the design deliberately avoids type inference, astroid's main advantage
over the stdlib `ast` goes unused. What pylint buys is everything around the
check: named message symbols (`# pylint: disable=complex-type-annotation` rather
than `# noqa: TAE001`), a message catalogue visible to `--list-msgs`, typed
options with config-file support, confidence levels, and
`pylint.testutils.CheckerTestCase` as a ready-made harness.

If you are already on flake8, note that
[flake8-annotations-complexity](https://github.com/best-doctor/flake8-annotations-complexity)
covers the nesting metric today (`TAE002`/`TAE003`) with zero code. Its gaps,
and the reason this checker exists anyway:

- it has no `ast.BinOp` case, so PEP 604 unions are invisible —
  `tuple[int, int] | None` scores **1** there and **2** here (pinned in
  `tests/test_depth.py::test_pep604_union_is_not_free`);
- no concept of type aliases, so no laxer budget for alias bodies;
- no `NamedTuple` suggestion.

**ruff** cannot do this at all: it does not support third-party plugins. The FAQ
still says *"Ruff does not yet support third-party plugins, though a plugin
system is within-scope for the project"*; the meta issue
([astral-sh/ruff#283](https://github.com/astral-sh/ruff/issues/283)) has been
open since 2022, and as of late 2025 the maintainers described the design as
discussed but unstarted. Ruff has reimplemented 50+ flake8 plugins natively, but
the `TAE` rules are not among them, so there is no built-in ruff rule for this
either.

## Known limits

- The head of a construct is recognised syntactically, with a bare-name fallback
  (`Optional` is assumed to mean `typing.Optional` even with no visible import,
  because re-exports and `if TYPE_CHECKING` blocks are common). A user-defined
  class literally named `Optional` or `Literal` is therefore mis-classified.
  Tested and accepted.
- `.pyi` stubs are skipped entirely.
- Not checked: `NewType("X", ...)`, `TypeVar(bound=...)`, `cast("...", x)`, and
  `# type:` comments.
- Annotations nested deeper than 32 levels, or larger than 2000 nodes, stop being
  walked and are reported as "over 32" rather than with an exact number.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/pylint --load-plugins=pylint_complex_struct pylint_complex_struct tests
```

`pylint_complex_struct/depth.py` holds the metric and imports no pylint, so it is
testable with bare astroid; `checker.py` holds the pylint plumbing.
