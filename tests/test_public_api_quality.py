"""Package docstrings stay free of backticks.

Ruff requires the docstrings and their parameter descriptions (rules D1 and
D417 in pyproject.toml) but has no rule against backticks, so that one lives here.
"""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "dj_hyperview"
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _backticked(source: str, path: str) -> list[str]:
    return [
        f"{path}:{getattr(node, 'lineno', 1)}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCUMENTED) and "`" in (ast.get_docstring(node) or "")
    ]


def test_package_docstrings_contain_no_backticks() -> None:
    offenders = [
        hit
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        for hit in _backticked(
            path.read_text(encoding="utf-8"), str(path.relative_to(PACKAGE_ROOT))
        )
    ]

    assert offenders == []


def test_backticks_are_found_at_every_docstring_level() -> None:
    source = (
        '"""Uses `a`."""\n'
        "class C:\n"
        '    """Uses `b`."""\n'
        "    def f(self):\n"
        '        """Uses `c`."""\n'
    )

    assert len(_backticked(source, "sample.py")) == 3
