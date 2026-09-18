"""Run the real pylint binary against the fixture, plugin and all."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from typing import NamedTuple

FIXTURE = pathlib.Path(__file__).parent / "functional" / "complex_annotations.py"
SYMBOLS = "complex-type-annotation,complex-type-alias,tuple-should-be-namedtuple"


class Expectation(NamedTuple):
    """One `# [symbol]` marker in the fixture."""

    line: int
    symbol: str


def expected_from_fixture() -> list[Expectation]:
    """Every `# [symbol]` marker in the fixture, with its line number."""
    expected = []
    for number, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines(), start=1):
        for symbol in re.findall(r"#\s*\[([a-z-]+)\]", line):
            expected.append(Expectation(number, symbol))
    return sorted(expected)


def test_fixture_declares_what_it_expects() -> None:
    assert expected_from_fixture(), "the fixture lost its expectation markers"


def test_end_to_end() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pylint",
            "--load-plugins=pylint_complex_struct",
            "--disable=all",
            f"--enable={SYMBOLS}",
            "--output-format=json2",
            str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Traceback" not in result.stderr, result.stderr
    payload = json.loads(result.stdout)
    actual = sorted(Expectation(m["line"], m["symbol"]) for m in payload["messages"])
    assert actual == expected_from_fixture()
