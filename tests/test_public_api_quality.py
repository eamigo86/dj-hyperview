"""Package docstrings stay free of backticks and cover the whole exported API.

Ruff requires the docstrings and their parameter descriptions (rules D1 and
D417 in pyproject.toml) but has no rule against backticks, and it treats a module
whose name starts with an underscore as private, so a public member inherited from
one is checked here too.
"""

import ast
import importlib
import inspect
import sys
from functools import cached_property
from pathlib import Path
from types import FunctionType

import pytest

from tests.test_database_app import run_isolated

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


def exporting_modules(root: Path = PACKAGE_ROOT) -> list[str]:
    """Return the dotted name of every module under the root that sets __all__."""
    names = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Assign)
            and any(getattr(target, "id", "") == "__all__" for target in node.targets)
            for node in tree.body
        ):
            parts = [root.name, *path.relative_to(root).with_suffix("").parts]
            names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _function(member: object) -> FunctionType | None:
    if isinstance(member, (classmethod, staticmethod)):
        member = member.__func__
    elif isinstance(member, property):
        member = member.fget
    elif isinstance(member, cached_property):
        member = member.func
    return member if isinstance(member, FunctionType) else None


def undocumented_exports(
    module_names: list[str], root: Path = PACKAGE_ROOT
) -> list[str]:
    """Return what the exports of these modules leave without a docstring.

    That is each exported class and function, and every public function, method
    and property (a dunder method counts) on the classes along the MRO of an
    exported class whose code lives under the root. Methods that Python generates
    for a dataclass or a protocol, and ones a base class from another package
    defines, are not ours to document.
    """
    missing: set[str] = set()
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for export in module.__all__:
            exported = getattr(module, export)
            if not (inspect.isclass(exported) or inspect.isroutine(exported)):
                continue
            if not exported.__doc__:
                missing.add(f"{module_name}.{export}")
            for klass in getattr(exported, "__mro__", ()):
                for name, member in vars(klass).items():
                    function = _function(member)
                    if (
                        function is not None
                        and not function.__doc__
                        and (not name.startswith("_") or name.startswith("__"))
                        and Path(function.__code__.co_filename).is_relative_to(root)
                    ):
                        missing.add(f"{klass.__module__}.{klass.__qualname__}.{name}")
    return sorted(missing)


def test_the_exporting_modules_are_found_by_their_all() -> None:
    assert {
        "dj_hyperview",
        "dj_hyperview.realtime",
        "dj_hyperview.sources",
        "dj_hyperview.contrib.database.services",
    } <= set(exporting_modules())


def test_undocumented_exports_are_found_through_private_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "walk_sample.py").write_text(
        "from dataclasses import dataclass\n"
        "__all__ = ['Public', 'Data', 'documented', 'bare']\n"
        "class _Base:\n"
        '    """Doc."""\n'
        "    def inherited(self): pass\n"
        "    @property\n"
        "    def value(self): pass\n"
        "    @classmethod\n"
        "    def make(cls): pass\n"
        "    def __iter__(self): pass\n"
        "    def _hidden(self): pass\n"
        '    def fine(self):\n        """Doc."""\n'
        'class Public(_Base):\n    """Doc."""\n'
        '@dataclass\nclass Data:\n    """Doc."""\n    number: int\n'
        'def documented():\n    """Doc."""\n'
        "def bare(): pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        found = undocumented_exports(["walk_sample"], tmp_path)
    finally:
        sys.modules.pop("walk_sample", None)

    assert found == [
        "walk_sample._Base.__iter__",
        "walk_sample._Base.inherited",
        "walk_sample._Base.make",
        "walk_sample._Base.value",
        "walk_sample.bare",
    ]


def test_every_exported_name_and_member_has_a_docstring() -> None:
    # The contrib modules need their apps installed, so one process loads them all.
    result = run_isolated(
        "tests.settings_database_admin",
        "import django; django.setup()\n"
        "from tests.test_public_api_quality import (\n"
        "    exporting_modules, undocumented_exports)\n"
        "print(*undocumented_exports(exporting_modules()), sep=chr(10))\n",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == []
