# pylint-complex-struct

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![pylint](https://img.shields.io/badge/pylint-4.0%2B-green.svg)](https://pylint.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A pylint plugin that flags over-nested type annotations and pushes them towards type
aliases, `NamedTuple`s and `TypedDict`s.

```python
# flagged
def load_records() -> tuple[list[dict[str, Any]], dict[str, Any]]: ...

# fixed
type Record  = dict[str, Any]
type Summary = dict[str, Any]

class LoadResult(NamedTuple):
    records: list[Record]
    summary: Summary

def load_records() -> LoadResult: ...
```

Nothing in pylint 4 checks this — `pylint.extensions.typing` only covers redundant and
deprecated typing constructs — and ruff has no equivalent rule.

## Table of contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Messages](#messages)
- [Configuration](#configuration)
- [How the metric works](#how-the-metric-works)
- [Adopting it on an existing codebase](#adopting-it-on-an-existing-codebase)
- [Comparison with flake8 and ruff](#comparison-with-flake8-and-ruff)
- [Known limitations](#known-limitations)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Installation

Requires Python 3.10+ and pylint 4.0+.

```bash
pip install git+https://github.com/dgutson/pylint-complex-struct.git
```

Or from a checkout:

```bash
git clone https://github.com/dgutson/pylint-complex-struct.git
cd pylint-complex-struct
pip install -e .
```

## Quick start

Pylint has no entry-point autoloading, so the plugin must be named explicitly:

```bash
pylint --load-plugins=pylint_complex_struct yourpackage
```

To survey an existing codebase with only this plugin's output:

```bash
pylint --load-plugins=pylint_complex_struct --disable=all \
       --enable=complex-type-annotation,complex-type-alias,tuple-should-be-namedtuple \
       yourpackage
```

Or configure it once in `pyproject.toml`:

```toml
[tool.pylint.main]
load-plugins = ["pylint_complex_struct"]

[tool.pylint."complex-struct"]
max-annotation-complexity = 2
```

The config section is the checker's name, `complex-struct`, which needs quoting in TOML
because of the hyphen. The equivalent in `pylintrc` is `[complex-struct]`, and in
`setup.cfg` or `tox.ini` it is `[pylint.complex-struct]`.

<details>
<summary>pre-commit hook</summary>

```yaml
repos:
  - repo: local
    hooks:
      - id: pylint-complex-struct
        name: complex type annotations
        entry: pylint --load-plugins=pylint_complex_struct
        language: system
        types: [python]
```

A `local`/`system` hook is simplest, because the plugin has to be importable in the same
environment as pylint.
</details>

<details>
<summary>CI</summary>

```yaml
- run: pip install pylint git+https://github.com/dgutson/pylint-complex-struct.git
- run: pylint --load-plugins=pylint_complex_struct --disable=all
         --enable=complex-type-annotation,complex-type-alias,tuple-should-be-namedtuple
         yourpackage
```

All three messages are in the refactor category, so a clean run exits `0` and a run with
findings sets bit 3 (exit status `8`).
</details>

## Messages

| ID | Symbol | Fires on |
|---|---|---|
| `R9501` | `complex-type-annotation` | an annotation deeper than `max-annotation-complexity`, or with more terms than `max-annotation-terms` |
| `R9502` | `complex-type-alias` | the *body* of a type alias, over the laxer `max-alias-complexity` |
| `R9503` | `tuple-should-be-namedtuple` | a return annotation that is a heterogeneous fixed-size tuple |

At most one message is emitted per annotation site: the depth rule wins over the
`NamedTuple` suggestion, and an alias body only ever produces `R9502`.

Silence an individual case as you would any pylint message:

```python
def legacy() -> tuple[dict[str, Any], list[dict[str, str]]]:  # pylint: disable=complex-type-annotation
    ...
```

### `R9503` in detail

A returned fixed-size tuple whose elements differ forces every call site to unpack
positionally and re-invent names for the fields:

```python
def load() -> tuple[Config, int]: ...     # R9503
def coords() -> tuple[int, int]: ...      # silent: a fixed-size vector
def rows() -> tuple[str, ...]: ...        # silent: a homogeneous sequence
def items() -> list[tuple[str, int]]: ... # silent: the dict.items() shape
```

Returns only, by default. A parameter typed `tuple[str, int]` is usually pass-through, and
the caller already has the values named.

## Configuration

All options live under `[tool.pylint."complex-struct"]` and work as ordinary pylint options
on the command line (`--max-annotation-complexity=3`).

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

The default budget of `2` is stricter than the flake8 equivalent's `3`. If the first run on
an existing codebase is too loud, set `max-annotation-complexity = 3`.

## How the metric works

Depth counts subscript levels, and **a name is always a leaf**:

```
int                          1
dict[str, Any]               2      <- legal by default
list[dict[str, Any]]         3      <- flagged
type Row = dict[str, Any]
type Table = list[Row]
dict[str, Table]             2      <- legal: extracting the alias fixed it
```

That last line is the whole design. The metric is purely syntactic and never asks astroid
what a name refers to, so pulling a subtree out into an alias mechanically brings the score
back within budget. A metric that expanded aliases would score the fixed code exactly like
the original — the checker could never be satisfied, and there would be no legal way to
write the type at all. `tests/test_no_inference.py` enforces this by grepping the source.

It also means results do not depend on which third-party packages happen to be installed,
so CI and your laptop agree.

### What does not count as nesting

| Construct | Treatment | Why |
|---|---|---|
| `X \| None`, `Optional[X]` | transparent | Nullability is a bit on a shape, not a shape to decompose. Aliasing it away hides optionality at the call site. Configurable. |
| `A \| B`, `Union[A, B]` | one level | A real branch the reader must hold. Both spellings share one code path. |
| `Callable[[A, B], R]` | the param bracket is free; param *types* count | The bracket is mandatory syntax, not chosen nesting. Configurable. |
| `Literal["a", "b"]` | leaf, contents never walked | Members are values, not types; there is nothing to extract. |
| `Annotated[T, meta]` | transparent, metadata never walked | Metadata is arbitrary runtime objects. Every Pydantic/FastAPI codebase would otherwise light up. |
| `Final`, `ClassVar`, `Required`, `NotRequired`, `ReadOnly`, `Unpack`, `TypeGuard`, `TypeIs`, `InitVar` | transparent | They describe how a name is used, not what shape it holds. |
| `*tuple[int, str]` | transparent | Must score the same as `Unpack[tuple[int, str]]`. |
| `...` in `tuple[int, ...]` | contributes nothing | A marker, not a type. |
| class bases | never visited | An alias cannot cleanly replace a base. |

Quoting is **not** an escape hatch: `-> "dict[str, list[tuple[int, int]]]"` scores the same
as the unquoted form. A forward reference that will not parse (`x: "the widget id"`) scores
as a leaf and is silently ignored.

### Type aliases

Alias bodies get their own, laxer budget, because absorbing structure is what an alias is
for — but hiding one unreadable structure behind a name has only moved the problem:

```python
type Row   = dict[str, Any]                       # fine
type Table = list[Row]                            # composing is free
type Blob  = dict[str, list[dict[str, Any]]]      # R9502: depth 4 > 3
```

`type X = ...` (PEP 695) and `X: TypeAlias = ...` (PEP 613) are both recognised.
Unannotated `Rows = dict[str, int]` is opt-in via `check-implicit-type-aliases`, because
`rows = cache["key"]` is also an assignment whose value is a subscript and there is no sound
syntactic way to tell them apart in general. A SCREAMING_CASE target is skipped: by PEP 8
that is a constant, so `PEELABLE = TRANSPARENT | ANNOTATED` is a frozenset union rather than
a union type.

## Adopting it on an existing codebase

1. Survey first with `--disable=all --enable=...` so the output is only this plugin.
2. If the count is large, start at `max-annotation-complexity = 3` and ratchet down to `2`
   once the depth-4 cases are gone.
3. Fix repeated shapes before one-offs — a single alias usually clears several sites.
   `--output-format=json2` piped through a counter tells you which shapes repeat.
4. Widen `namedtuple-check-scope` beyond `returns` only after the depth rule is quiet, since
   the depth rule masks the NamedTuple suggestion on any site that is also too deep.

## Comparison with flake8 and ruff

**flake8** would be marginally simpler to bootstrap and worse to live with. A flake8 plugin
is a class taking `(tree, filename)` with a `run()` yielding `(line, col, "XXX001 text",
type(self))` — perhaps 30 lines less scaffolding. But roughly 70% of this project is the
depth function, which would be identical, and since the design deliberately avoids type
inference, astroid's main advantage over the stdlib `ast` goes unused. What pylint buys is
everything around the check: named message symbols (`# pylint:
disable=complex-type-annotation` rather than `# noqa: TAE001`), a message catalogue visible
to `--list-msgs`, typed options with config-file support, confidence levels, and
`pylint.testutils.CheckerTestCase` as a ready-made harness.

If you are already on flake8,
[flake8-annotations-complexity](https://github.com/best-doctor/flake8-annotations-complexity)
covers the nesting metric today (`TAE002`/`TAE003`) with zero code. Its gaps:

- no `ast.BinOp` case, so PEP 604 unions are invisible — `tuple[int, int] | None` scores
  **1** there and **2** here (pinned by `tests/test_depth.py::test_pep604_union_is_not_free`);
- no concept of type aliases, so no laxer budget for alias bodies;
- no `NamedTuple` suggestion.

**ruff** cannot do this at all: it does not support third-party plugins. The meta issue
([astral-sh/ruff#283](https://github.com/astral-sh/ruff/issues/283)) has been open since
2022, and as of late 2025 the maintainers described the design as discussed but unstarted.
Ruff has reimplemented 50+ flake8 plugins natively, but the `TAE` rules are not among them.

## Known limitations

- The head of a construct is recognised syntactically, with a bare-name fallback
  (`Optional` is assumed to mean `typing.Optional` even with no visible import, because
  re-exports and `if TYPE_CHECKING` blocks are common). A user-defined class literally named
  `Optional` or `Literal` is therefore mis-classified. Tested and accepted.
- `.pyi` stubs are skipped entirely.
- Not checked: `NewType("X", ...)`, `TypeVar(bound=...)`, `cast("...", x)`, and `# type:`
  comments.
- Annotations nested deeper than 32 levels, or larger than 2000 nodes, stop being walked and
  are reported as "over 32" rather than with an exact number.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/pylint --load-plugins=pylint_complex_struct pylint_complex_struct tests
```

The plugin is run against its own source as part of the test discipline, and is expected to
stay clean at 10.00/10.

**Layout.** `pylint_complex_struct/depth.py` holds the metric and `names.py` the syntactic
head resolution; neither imports pylint, so both stay unit-testable with bare astroid.
`checker.py` holds the pylint plumbing. See [CLAUDE.md](CLAUDE.md) for the architectural
invariants and [ROADMAP.md](ROADMAP.md) for what is planned.

## Contributing

Issues and pull requests are welcome. Two things to know before opening one:

- **The metric must never infer.** `depth.py` and `names.py` may not import pylint and may
  not call `.infer()`, `.inferred()`, `safe_infer()`, `.lookup()`, `object_type()` or
  `.getattr()`. `tests/test_no_inference.py` greps for exactly these. Expanding aliases
  during scoring would make the rule unsatisfiable, so it will not be accepted.
- New messages use the `95xx` range; pylint reserves 51–99 as the first two digits for
  third-party checkers.

Please make sure `pytest` and the self-lint above both pass.

## License

[MIT](LICENSE) © Daniel Gutson
