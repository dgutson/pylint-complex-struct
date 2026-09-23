# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A pylint plugin that flags over-nested type annotations and pushes them towards type
aliases, `NamedTuple`s and `TypedDict`s. Three messages ship today:

| ID | Symbol | Fires on |
|---|---|---|
| `R9501` | `complex-type-annotation` | an annotation over the depth or term budget |
| `R9502` | `complex-type-alias` | the *body* of a type alias, over `max-alias-complexity` |
| `R9503` | `tuple-should-be-namedtuple` | a return annotation that is a heterogeneous fixed-size tuple |

`README.md` has the metric, the rationale for every construct, and the comparison with
flake8 and ruff. `ROADMAP.md` has the invocation routes and what is planned next.

## Commands

**There is no system pylint or pytest here — always use `.venv/bin/...`.** A bare `pylint`
or `pytest` is the wrong command in this repo.

```bash
.venv/bin/pytest -q                                    # 130 passed
.venv/bin/pylint --load-plugins=pylint_complex_struct pylint_complex_struct   # 10.00/10
```

Both must be green before committing. The second is the dogfooding run: the checker is a
fixed point against its own source and has to stay one.

Rebuilding the environment: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`.

## Layout

| File | Holds |
|---|---|
| `pylint_complex_struct/depth.py` | the metric — `measure`, `peel`, `namedtuple_candidate`, `Policy`, `Verdict` |
| `pylint_complex_struct/names.py` | syntactic head resolution — `ImportMap`, `resolve_head`, `canonical`, the `TRANSPARENT`/`PEELABLE` sets |
| `pylint_complex_struct/checker.py` | pylint plumbing — `ComplexStructChecker`, `msgs`, `options`, the visitors |
| `pylint_complex_struct/__init__.py` | `register` |

## The hard architectural rule

`depth.py` and `names.py` **must not import pylint and must never infer.** Forbidden, and
grepped for by `tests/test_no_inference.py`: `.infer(`, `.inferred(`, `safe_infer(`,
`.lookup(`, `object_type(`, `.getattr(`.

**A name is always a leaf, whatever it expands to.** This is not a shortcut, it is what
makes the rule satisfiable. If the metric expanded aliases, then `dict[str, Table]` would
score like the structure `Table` names — so extracting an alias would not clear the
message, and there would be no legal way to write the type at all. The checker could never
be satisfied.

```python
type Row   = dict[str, Any]
type Table = list[Row]
dict[str, Table]        # depth 2, legal — extracting the alias is what fixed it
```

`tests/test_checker.py::TestComplexStructChecker::test_correctly_factored_code_is_silent`
is the behavioural guard behind the greps. If it starts failing, someone has taught the
metric to expand aliases.

A side benefit worth keeping: the score does not depend on which third-party packages
happen to be installed, so CI and a laptop always agree.

## Adding a message

- **Msgid range is `95xx`.** Pylint requires the first two digits of a custom checker's
  msgid to be 51–99. Next free is `R9504`.
- Checker `name = "complex-struct"`, so config lands in `[tool.pylint."complex-struct"]`
  (TOML), `[complex-struct]` (pylintrc), `[pylint.complex-struct]` (setup.cfg, tox.ini).
- **At most one message per annotation site.** `_check` returns after emitting depth or
  terms; the heuristics in `_check_heuristics` only run on a site already within budget; an
  alias body only ever yields `R9502`. A new rule joins `_check_heuristics` under that same
  priority.
- **All emission goes through `ComplexStructChecker._report`**, which dedups on
  `MessageKey(symbol, lineno, col_offset)` and stamps `confidence=HIGH`. It is the only
  caller of `add_message` in the package, and the recursive walk cannot emit at all because
  `depth.py` does not import pylint. Keep it that way.
- A new scope option must be added to `VALID_SCOPES` (`checker.py:17`) and validated in
  `open()`, which raises on an unknown value rather than ignoring it.
- **Option defaults are written only in `Policy`** (`depth.py`). The `options` table reads
  them through `DEFAULTS = Policy()`; never put a literal `"default"` there.
  `test_option_defaults_are_the_policy_defaults` fails if the two drift apart.
- `pyproject.toml` sets `ignore-paths = ["tests/functional/.*"]`. That fixture is
  deliberately full of violations — do not "fix" it.

## Judgment calls already settled

Do not relitigate these; each is tested.

- `Optional[X]` / `X | None` is transparent — nullability is a bit on a shape, not a shape.
- `Literal` contents and `Annotated` metadata are never walked.
- `Callable`'s parameter bracket is free; its parameter types are not.
- Quoting is not an escape hatch — forward refs are parsed and scored the same.
- `.pyi` stubs are skipped entirely.
- `list[tuple[str, int]]` is deliberately *not* a NamedTuple candidate: it is the
  `dict.items()` shape.
- SCREAMING_CASE assignment targets are skipped — by PEP 8 those are constants, so
  `PEELABLE = TRANSPARENT | ANNOTATED` is a frozenset union, not a union type.
- Depth is clamped at `MAX_LEVEL = 32` via `Verdict.truncated`, and the message says "over
  32". Returning the cap instead let recursion unwinding inflate the reported number.

## Testing notes

- `astroid.extract_node('"some string"')` does not work — a lone string at module level is
  a docstring. Use the `quoted()` helper in `tests/test_depth.py`, which builds
  `def _f() -> '<text>': ...` and reads `.returns`.
- Very large unions must be built by hand with `nodes.BinOp(...)` / `postinit`: astroid's
  own parser raises `RecursionError` first, and building it manually is what actually
  proves `flatten_union` is iterative.
- PEP 695 (`type X = ...`) tests skip below 3.12 via the `needs_pep695` marker in
  `tests/test_checker.py`.
