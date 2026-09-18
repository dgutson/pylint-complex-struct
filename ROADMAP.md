# pylint-complex-struct — what it is, how to run it, where it is going

## What it is

A pylint plugin that flags over-nested type annotations and pushes them towards
type aliases, `NamedTuple`s and `TypedDict`s. It exists because annotations like
`tuple[list[dict[str, Any]], dict[str, Any]]` keep accumulating in real code, and
nothing in pylint 4 checks for them.

Three messages:

| ID | Symbol | Fires on |
|---|---|---|
| `R9501` | `complex-type-annotation` | an annotation over the depth or term budget |
| `R9502` | `complex-type-alias` | the *body* of a type alias, over its laxer budget |
| `R9503` | `tuple-should-be-namedtuple` | a return annotation that is a heterogeneous fixed-size tuple |

Depth counts subscript levels and **a name is always a leaf**: `int` is 1,
`dict[str, Any]` is 2, `list[dict[str, Any]]` is 3, and `dict[str, Table]` is 2
however deep the `Table` alias goes. See `README.md` for the full metric, the
rationale for each construct, and the comparison with flake8 and ruff.

## Installing

```bash
pip install -e /path/to/pylint-complex-struct     # from a checkout
```

It needs Python 3.10+ and pylint 4.0+. Nothing is registered automatically:
pylint has no entry-point autoloading, so every invocation route below comes down
to getting `pylint_complex_struct` into pylint's `load-plugins` list.

## Invoking it

### Command line

```bash
# alongside all of pylint's own checks
pylint --load-plugins=pylint_complex_struct yourpackage

# this plugin only — useful for a first survey of an existing codebase
pylint --load-plugins=pylint_complex_struct --disable=all \
       --enable=complex-type-annotation,complex-type-alias,tuple-should-be-namedtuple \
       yourpackage

# override an option inline
pylint --load-plugins=pylint_complex_struct --max-annotation-complexity=3 yourpackage

# machine-readable, for scripts and dashboards
pylint --load-plugins=pylint_complex_struct --output-format=json2 yourpackage
```

Options are ordinary pylint options once the plugin is loaded, so
`--max-annotation-complexity`, `--namedtuple-check-scope` and the rest all work on
the command line. Exit status is pylint's usual bitmask: these are all
refactor-category messages, so a clean run is `0` and a run with findings sets
bit 3 (`8`).

### pyproject.toml

```toml
[tool.pylint.main]
load-plugins = ["pylint_complex_struct"]

[tool.pylint."complex-struct"]
max-annotation-complexity = 2
max-alias-complexity = 3
namedtuple-check-scope = ["returns"]
```

The section name is the checker's own name, `complex-struct`, and it needs
quoting in TOML because of the hyphen. Note that pylint only treats a
`pyproject.toml` as a config file if it actually contains a `[tool.pylint...]`
section.

### pylintrc / .pylintrc

```ini
[MAIN]
load-plugins=pylint_complex_struct

[complex-struct]
max-annotation-complexity=2
max-alias-complexity=3
namedtuple-check-scope=returns
```

`pylintrc.toml` and `.pylintrc.toml` take the TOML spelling above instead.

### setup.cfg and tox.ini

Same INI keys, with every section prefixed by `pylint.`:

```ini
[pylint.MAIN]
load-plugins=pylint_complex_struct

[pylint.complex-struct]
max-annotation-complexity=2
```

A `setup.cfg` or `tox.ini` without a `[pylint]` or `[pylint.*]` section is
skipped, so adding these sections is what makes pylint notice the file at all.

### Pointing at a config explicitly

```bash
pylint --rcfile=ci/pylintrc yourpackage
PYLINTRC=/etc/pylintrc pylint yourpackage
```

### Where pylint looks, in order

1. `pylintrc`, `pylintrc.toml`, `.pylintrc`, `.pylintrc.toml`, then
   `pyproject.toml`, `setup.cfg`, `tox.ini` in the current directory
2. walking up out of the current package (while `__init__.py` keeps existing),
   looking for the four rc names
3. the nearest `pyproject.toml` up the tree, stopping at a `.git` or `.hg`
4. `$PYLINTRC`, or `~/.pylintrc`, or `~/.config/pylintrc`

`--rcfile` overrides the search entirely.

### Silencing individual cases

```python
def legacy() -> tuple[dict[str, Any], list[dict[str, str]]]:  # pylint: disable=complex-type-annotation
    ...
```

Block- and file-level `# pylint: disable=` comments work as they do for any
pylint message, and messages can be disabled wholesale by symbol or by ID in any
of the config files above.

### pre-commit

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

A `local`/`system` hook is the simplest route because the plugin has to be
importable in the same environment as pylint; a `mirrors-pylint` hook needs
`additional_dependencies: [pylint-complex-struct]`.

### CI

```yaml
- run: pip install pylint pylint-complex-struct
- run: pylint --load-plugins=pylint_complex_struct --disable=all
         --enable=complex-type-annotation,complex-type-alias,tuple-should-be-namedtuple
         yourpackage
```

### Editors

Anything that shells out to pylint (VS Code's Pylint extension, PyCharm's
external tool, `flycheck`) picks the plugin up from the project config file, so
the `pyproject.toml` stanza above is usually all that is needed.

## Options

| Option | Type | Default |
|---|---|---|
| `max-annotation-complexity` | int | `2` |
| `max-alias-complexity` | int | `3` |
| `max-annotation-terms` | int | `7` |
| `count-optional-as-nesting` | yn | `n` |
| `count-union-as-nesting` | yn | `y` |
| `count-callable-params-as-nesting` | yn | `n` |
| `namedtuple-check-scope` | csv | `returns` |
| `min-namedtuple-fields` | int | `2` |
| `check-implicit-type-aliases` | yn | `n` |

`README.md` explains what each one does and why the defaults are what they are.

## Adopting it on an existing codebase

1. Survey first with `--disable=all --enable=...` so the output is only this
   plugin.
2. If the count is large, set `max-annotation-complexity = 3` (the
   flake8-annotations-complexity default) and ratchet down to `2` once the
   depth-4 cases are gone.
3. Fix the repeated shapes before the one-offs: a single alias usually clears
   several sites, and `--output-format=json2` piped through a counter tells you
   which shapes repeat.
4. Turn on `namedtuple-check-scope` beyond `returns` only after the depth rule is
   quiet, since the depth rule masks the NamedTuple suggestion on any site that
   is also too deep.

## Where it is going

Done and stable: the metric, the three messages, all nine options, 126 tests, and
a self-clean run against its own source.

Candidates, roughly in order of usefulness:

- **`R9504` `dict-should-be-typeddict`** — a str-keyed dict whose values are
  `Any`, `object` or a heterogeneous union, in return position. Deliberately left
  out of v0.1 as the noisiest of the family, but real codebases are full of
  `dict[str, Any]` payloads that the depth rule cannot see, and it is one message
  plus one scope option to add.
- **Type comments** — `# type: (...) -> ...` on functions and assignments. The
  node positions are synthetic enough that caret placement would be poor, which
  is why it is not in v0.1.
- **`NewType`, `TypeVar(bound=...)`, `cast("...")`** — annotation-shaped
  expressions that are currently not checked at all.
- **Publish to PyPI**, so `additional_dependencies` and CI installs stop needing a
  path.
- **Installing and developing with `uv`.** `uv sync` and `uv run pytest` in
  place of the `python3 -m venv` / `pip install -e '.[dev]'` dance the README
  still prescribes,
  `uv run pylint --load-plugins=pylint_complex_struct yourpackage` for
  consumers, and a committed `uv.lock` so a checkout is reproducible. Mostly
  documentation, plus moving the `dev` extra into a `[dependency-groups]` table
  so `uv sync` picks it up without an `--extra`. Two routes do not follow:
  `pre-commit` resolves `additional_dependencies` through pip, and
  `uv add pylint-complex-struct` wants an index, so this reads better once
  **Publish to PyPI** is done.
- **CI matrix on 3.10–3.13.** The PEP 695 tests already skip below 3.12; nothing
  else is version-sensitive, but it is untested off 3.12 today.
- **A `--suggest` mode** printing a concrete alias for each finding
  (`type ArcResult = tuple[RowMap, IndexMap]`). Pylint cannot apply fixes, so
  this would be message text only.

Not planned: expanding aliases during scoring. That would make the rule
unsatisfiable, and `tests/test_no_inference.py` exists to keep it from creeping
in.
