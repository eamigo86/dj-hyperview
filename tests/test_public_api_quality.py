"""Contract tests for the package-owned public API quality audit."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "dj_hyperview"
_DEFINITION = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_DOCUMENTABLE = (ast.Module, *_DEFINITION)


def _diagnostic(path: str, node: ast.AST, symbol: str, reason: str) -> str:
    return f"{path}:{getattr(node, 'lineno', 1)}:{symbol}: {reason}"


def _literal_exports(node: ast.AST | None) -> set[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)) or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in node.elts
    ):
        return None
    return {item.value for item in node.elts}


def _uses_exports(node: ast.AST) -> bool:
    for candidate in ast.walk(node):
        if isinstance(candidate, ast.Name) and candidate.id == "__all__":
            return True
        if isinstance(candidate, (ast.Global, ast.Nonlocal)) and "__all__" in (
            candidate.names
        ):
            return True
        if isinstance(candidate, ast.alias) and (
            candidate.asname == "__all__" or candidate.name == "__all__"
        ):
            return True
        if (
            isinstance(candidate, ast.Subscript)
            and isinstance(candidate.value, ast.Call)
            and isinstance(candidate.value.func, ast.Name)
            and candidate.value.func.id == "globals"
            and not candidate.value.args
            and not candidate.value.keywords
            and isinstance(candidate.slice, ast.Constant)
            and candidate.slice.value == "__all__"
        ):
            return True
    return False


def _exports(tree: ast.Module, path: str) -> tuple[set[str], list[str]]:
    exports: set[str] = set()
    assigned = False
    issues: list[str] = []

    def unresolved(node: ast.AST) -> None:
        issues.append(
            _diagnostic(path, node, "<module>", "__all__ cannot be resolved statically")
        )

    for node in tree.body:
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            value = node.value
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            addition = _literal_exports(node.value)
            if not assigned or not isinstance(node.op, ast.Add) or addition is None:
                unresolved(node)
            else:
                exports.update(addition)
            continue
        else:
            if _uses_exports(node):
                unresolved(node)
            continue

        replacement = _literal_exports(value)
        if replacement is None:
            unresolved(node)
        else:
            exports = replacement
            assigned = True
    return exports, issues


def _is_public(name: str, exports: set[str]) -> bool:
    return not name.startswith("_") or name in exports


def _ordered(issues: list[str]) -> list[str]:
    def key(issue: str) -> tuple[str, int, str]:
        path, line, detail = issue.split(":", 2)
        return path, int(line), detail

    return sorted(issues, key=key)


def _audit_text(source: str, path: str = "sample.py") -> list[str]:
    tree = ast.parse(source, filename=path)
    exports, issues = _exports(tree, path)
    if ast.get_docstring(tree) is None:
        issues.append(
            _diagnostic(path, tree, "<module>", "runtime module lacks a docstring")
        )

    for node in ast.walk(tree):
        if isinstance(node, _DOCUMENTABLE):
            docstring = ast.get_docstring(node)
            if docstring and "`" in docstring:
                symbol = getattr(node, "name", "<module>")
                issues.append(
                    _diagnostic(path, node, symbol, "docstring uses backticks")
                )

    for node in tree.body:
        if not isinstance(node, _DEFINITION) or not _is_public(node.name, exports):
            continue
        if ast.get_docstring(node) is None:
            kind = "class" if isinstance(node, ast.ClassDef) else "callable"
            issues.append(
                _diagnostic(path, node, node.name, f"public {kind} lacks a docstring")
            )
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if (
                    isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not method.name.startswith("_")
                    and ast.get_docstring(method) is None
                ):
                    issues.append(
                        _diagnostic(
                            path,
                            method,
                            f"{node.name}.{method.name}",
                            "public method lacks a docstring",
                        )
                    )
    return _ordered(issues)


def _audit_package() -> list[str]:
    issues: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "migrations" in path.parts or source.lstrip().startswith("# Generated by"):
            continue
        relative = path.relative_to(PACKAGE_ROOT.parent.parent).as_posix()
        issues.extend(_audit_text(source, relative))
    return _ordered(issues)


def test_public_api_docstrings_have_no_debt_or_allowlist() -> None:
    """Require documented public symbols and backtick-free package docstrings."""
    assert _audit_package() == []


def test_auditor_reports_deterministic_docstring_failures() -> None:
    """Prove all public symbol levels and private backticks are diagnosed."""
    source = '''class Public:
    def run(self):
        pass

def exposed():
    pass

def documented():
    """Uses `public` syntax."""

def _private():
    """Uses `private` syntax."""
'''
    assert _audit_text(source) == [
        "sample.py:1:<module>: runtime module lacks a docstring",
        "sample.py:1:Public: public class lacks a docstring",
        "sample.py:2:Public.run: public method lacks a docstring",
        "sample.py:5:exposed: public callable lacks a docstring",
        "sample.py:8:documented: docstring uses backticks",
        "sample.py:11:_private: docstring uses backticks",
    ]


def test_auditor_honors_exports_without_requiring_private_symbols() -> None:
    """Prove explicit exports are public while ordinary private names remain private."""
    source = '''"""Documented module."""
__all__ = ["_exported"]

def _exported():
    pass

def _internal():
    pass
'''
    assert _audit_text(source) == [
        "sample.py:4:_exported: public callable lacks a docstring"
    ]


def test_auditor_resolves_annotated_reassigned_and_incremental_exports() -> None:
    """Require static export composition to match the final module value."""
    source = '''"""Documented module."""
__all__: list[str] = ["_discarded"]
__all__ = ("_kept",)
__all__ += ["_added"]

def _discarded():
    pass

def _kept():
    pass

def _added():
    pass
'''
    assert _audit_text(source) == [
        "sample.py:9:_kept: public callable lacks a docstring",
        "sample.py:12:_added: public callable lacks a docstring",
    ]


def test_auditor_fails_closed_for_unresolved_export_declarations() -> None:
    """Reject dynamic assignments and annotated declarations without values."""
    assert _audit_text('''"""Documented module."""
__all__ = names()
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
__all__: list[str]
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_dynamic_export_composition() -> None:
    """Reject incremental expressions and mutating calls on exports."""
    assert _audit_text('''"""Documented module."""
__all__ = ["public"]
__all__ += names()
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_nested_and_item_export_mutations() -> None:
    """Reject conditional declarations and item mutation of exports."""
    assert _audit_text('''"""Documented module."""
if enabled:
    __all__ = ["_conditional"]
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
__all__ = ["public"]
__all__[0] = "other"
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
__all__ = ["public"]
__all__.append("other")
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_when_exports_escape_through_an_alias() -> None:
    """Reject aliases that can mutate exports beyond static analysis."""
    assert _audit_text('''"""Documented module."""
__all__ = []
exports = __all__
exports.extend(["_hidden"])
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
exports = __all__ = []
exports.append("_hidden")
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_when_exports_reach_opaque_code() -> None:
    """Reject opaque calls and function-scoped export references."""
    assert _audit_text('''"""Documented module."""
__all__ = []
mutate(__all__)
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
def expose():
    """Return the export collection."""
    return __all__
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_reflective_export_access() -> None:
    """Reject obvious reflective access to the export collection."""
    assert _audit_text('''"""Documented module."""
exports = globals()["__all__"]
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_deleted_and_imported_exports() -> None:
    """Reject deleting or importing the export collection dynamically."""
    assert _audit_text('''"""Documented module."""
__all__ = []
del __all__
''') == [
        "sample.py:3:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
from another_module import __all__
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]
    assert _audit_text('''"""Documented module."""
def configure():
    """Declare dynamic exports."""
    global __all__
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]
