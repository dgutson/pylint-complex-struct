"""Guard the contract that makes the rule satisfiable.

If the metric ever expands an alias, code that has already been fixed scores
exactly like the code before the fix, and there is no legal way to write the
type at all. These greps are crude on purpose: they fail loudly the moment
someone reaches for inference.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from pylint_complex_struct import depth, names

FORBIDDEN = (
    r"\.infer\(",
    r"\.inferred\(",
    r"safe_infer\(",
    r"\.lookup\(",
    r"object_type\(",
    r"\.getattr\(",
)


@pytest.mark.parametrize("module", [depth, names], ids=["depth", "names"])
def test_module_never_infers(module: object) -> None:
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    offenders = [pattern for pattern in FORBIDDEN if re.search(pattern, source)]
    assert not offenders, f"{module.__name__} reaches for inference: {offenders}"


@pytest.mark.parametrize("module", [depth, names], ids=["depth", "names"])
def test_metric_does_not_depend_on_pylint(module: object) -> None:
    """The metric stays unit-testable with nothing but astroid."""
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+pylint\b", source, re.MULTILINE)
