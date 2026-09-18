"""A pylint checker for over-nested type annotations.

Load it with ``pylint --load-plugins=pylint_complex_struct``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker import ComplexStructChecker

if TYPE_CHECKING:
    from pylint.lint import PyLinter

__version__ = "0.1.0"
__all__ = ["ComplexStructChecker", "register"]


def register(linter: PyLinter) -> None:
    """Entry point called by pylint when the plugin is loaded."""
    linter.register_checker(ComplexStructChecker(linter))
