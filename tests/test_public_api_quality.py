"""Package docstrings stay free of backticks and cover the whole exported API.

Ruff requires the docstrings and their parameter descriptions (rules D1 and
D417 in pyproject.toml) but has no rule against backticks, and it treats a module
whose name starts with an underscore as private, so a public member inherited from
one is checked here too.
"""

import ast
import importlib
import inspect
import json
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
            isinstance(node, ast.Name)
            and node.id == "__all__"
            and isinstance(node.ctx, ast.Store)
            for node in ast.walk(tree)
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


def checked_names(
    module_names: list[str], root: Path = PACKAGE_ROOT
) -> dict[str, bool]:
    """Map each name the walk checks to whether it has a docstring.

    The names are each exported class and function, and every public function,
    method and property (a dunder method counts) on the classes along the MRO of an
    exported class whose code lives under the root. Methods that Python generates
    for a dataclass or a protocol (Python 3.14 also generates __annotate__ for any
    class with annotations), and ones a base class from another package defines,
    are not ours to document.
    """
    checked: dict[str, bool] = {}
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for export in module.__all__:
            exported = getattr(module, export)
            if not (inspect.isclass(exported) or inspect.isroutine(exported)):
                continue
            checked[f"{module_name}.{export}"] = bool(exported.__doc__)
            for klass in getattr(exported, "__mro__", ()):
                for name, member in vars(klass).items():
                    function = _function(member)
                    if (
                        function is not None
                        and function.__name__ != "__annotate__"
                        and (not name.startswith("_") or name.startswith("__"))
                        and Path(function.__code__.co_filename).is_relative_to(root)
                    ):
                        label = f"{klass.__module__}.{klass.__qualname__}.{name}"
                        checked[label] = bool(function.__doc__)
    return checked


def undocumented_exports(
    module_names: list[str], root: Path = PACKAGE_ROOT
) -> list[str]:
    """Return what the exports of these modules leave without a docstring."""
    checked = checked_names(module_names, root)
    return sorted(name for name, documented in checked.items() if not documented)


def test_the_exporting_modules_are_found_by_their_all() -> None:
    assert {
        "dj_hyperview",
        "dj_hyperview.realtime",
        "dj_hyperview.sources",
        "dj_hyperview.contrib.database.services",
    } <= set(exporting_modules())


def test_all_is_found_however_it_is_written(tmp_path: Path) -> None:
    package = tmp_path / "sample_pkg"
    package.mkdir()
    for name, source in {
        "plain": "__all__ = ['a']",
        "annotated": "__all__: list[str] = ['a']",
        "augmented": "from os.path import *\n__all__ += ['a']",
        "conditional": "if True:\n    __all__ = ['a']",
        "absent": "everything = ['a']\nprint(__all__)",
    }.items():
        (package / f"{name}.py").write_text(source + "\n")

    assert exporting_modules(package) == [
        "sample_pkg.annotated",
        "sample_pkg.augmented",
        "sample_pkg.conditional",
        "sample_pkg.plain",
    ]


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
        "import json\n"
        "from tests.test_public_api_quality import checked_names, exporting_modules\n"
        "modules = exporting_modules()\n"
        "print(json.dumps([modules, checked_names(modules)]))\n",
    )

    assert result.returncode == 0, result.stderr
    modules, checked = json.loads(result.stdout)
    # A walk that reached nothing would pass, so each module must have been reached.
    for module in modules:
        assert any(name.rpartition(".")[0] == module for name in checked), module
    assert [name for name, documented in checked.items() if not documented] == []
