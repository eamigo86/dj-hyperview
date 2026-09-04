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
        for _, value in ast.iter_fields(candidate):
            if value == "__all__" or (
                isinstance(value, (list, tuple)) and "__all__" in value
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
            if _uses_exports(node.annotation):
                unresolved(node)
                continue
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


def _is_public_method(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _is_type_reference(annotation: ast.expr) -> bool:
    return isinstance(annotation, ast.Name) or (
        isinstance(annotation, ast.Attribute) and _is_type_reference(annotation.value)
    )


def _is_type_expression(annotation: ast.expr) -> bool:
    if _is_type_reference(annotation):
        return True
    if isinstance(annotation, ast.Subscript):
        return _is_type_expression(annotation.value)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_type_expression(annotation.left) and _is_type_expression(
            annotation.right
        )
    return isinstance(annotation, ast.Constant) and annotation.value is None


def _is_valid_type_hint(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        expression = annotation.value.strip()
        if not expression:
            return False
        try:
            annotation = ast.parse(expression, mode="eval").body
        except SyntaxError:
            return False
    return _is_type_expression(annotation)


def _type_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    symbol: str,
) -> list[str]:
    parameters = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg is not None else []),
        *([node.args.kwarg] if node.args.kwarg is not None else []),
    ]
    issues: list[str] = []
    for parameter in parameters:
        if parameter.arg in {"self", "cls"}:
            continue
        if parameter.annotation is None:
            reason = f"public callable parameter '{parameter.arg}' lacks a type hint"
        elif not _is_valid_type_hint(parameter.annotation):
            reason = (
                f"public callable parameter '{parameter.arg}' has an invalid type hint"
            )
        else:
            continue
        issues.append(_diagnostic(path, node, symbol, reason))
    if node.returns is None:
        issues.append(
            _diagnostic(path, node, symbol, "public callable lacks a return type hint")
        )
    elif not _is_valid_type_hint(node.returns):
        issues.append(
            _diagnostic(
                path,
                node,
                symbol,
                "public callable has an invalid return type hint",
            )
        )
    return issues


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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(_type_issues(node, path, node.name))
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                symbol = f"{node.name}.{method.name}"
                if (
                    not method.name.startswith("_")
                    and ast.get_docstring(method) is None
                ):
                    issues.append(
                        _diagnostic(
                            path, method, symbol, "public method lacks a docstring"
                        )
                    )
                if _is_public_method(method.name):
                    issues.extend(_type_issues(method, path, symbol))
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
    def run(self) -> None:
        pass

def exposed() -> None:
    pass

def documented() -> None:
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

def _exported() -> None:
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

def _kept() -> None:
    pass

def _added() -> None:
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
def expose() -> object:
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


def test_auditor_fails_closed_for_locals_subscript_reflection() -> None:
    """Reject literal local-namespace export reflection."""
    assert _audit_text('''"""Documented module."""
exports = locals()["__all__"]
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_globals_get_reflection() -> None:
    """Reject export reflection through the global namespace getter."""
    assert _audit_text('''"""Documented module."""
exports = globals().get("__all__")
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_locals_get_reflection() -> None:
    """Reject export reflection through the local namespace getter."""
    assert _audit_text('''"""Documented module."""
exports = locals().get("__all__")
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_fails_closed_for_export_annotation_side_effects() -> None:
    """Reject an export annotation that reads the assigned collection."""
    assert _audit_text('''"""Documented module."""
__all__: inspect(__all__) = []
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_rejects_literal_reflection_without_api_enumeration() -> None:
    """Reject literal reflective forms while preserving static export literals."""
    for expression in (
        'vars()["__all__"]',
        'getattr(module, "__all__")',
        'setattr(module, "__all__", [])',
        "module.__all__",
    ):
        assert _audit_text(f'"""Documented module."""\nvalue = {expression}\n') == [
            "sample.py:2:<module>: __all__ cannot be resolved statically",
        ]

    assert (
        _audit_text('''"""Documented module."""
__all__ = ["__all__"]
__all__ += ("_documented",)

def _documented() -> None:
    """Provide a documented private export."""
''')
        == []
    )


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
def configure() -> None:
    """Declare dynamic exports."""
    global __all__
''') == [
        "sample.py:2:<module>: __all__ cannot be resolved statically",
    ]


def test_auditor_requires_every_public_parameter_and_return_type() -> None:
    """Diagnose every public parameter category and the return annotation."""
    source = '''"""Documented module."""

def exposed(positional_only, /, regular, *items, keyword_only, **options):
    """Expose every supported parameter category."""
'''
    assert _audit_text(source) == [
        "sample.py:3:exposed: public callable lacks a return type hint",
        "sample.py:3:exposed: public callable parameter 'items' lacks a type hint",
        "sample.py:3:exposed: public callable parameter 'keyword_only' "
        "lacks a type hint",
        "sample.py:3:exposed: public callable parameter 'options' lacks a type hint",
        "sample.py:3:exposed: public callable parameter 'positional_only' "
        "lacks a type hint",
        "sample.py:3:exposed: public callable parameter 'regular' lacks a type hint",
    ]


def test_auditor_types_async_decorated_and_relevant_dunder_methods() -> None:
    """Cover async, descriptors, method decorators and package-owned dunders."""
    source = '''"""Documented module."""

async def fetch(value: str):
    """Fetch a value asynchronously."""

class Public:
    """Provide representative package-owned methods."""

    def __init__(self, value: str):
        self.value = value

    @staticmethod
    def build(value: str):
        """Build one instance."""

    @classmethod
    def create(cls, value: str):
        """Create one instance."""

    @property
    def name(self):
        """Return the public name."""

    @name.setter
    def name(self, value):
        """Set the public name."""

    def __str__(self):
        return self.value

    def __call__(self, value: str):
        return value
'''
    assert _audit_text(source) == [
        "sample.py:3:fetch: public callable lacks a return type hint",
        "sample.py:9:Public.__init__: public callable lacks a return type hint",
        "sample.py:13:Public.build: public callable lacks a return type hint",
        "sample.py:17:Public.create: public callable lacks a return type hint",
        "sample.py:21:Public.name: public callable lacks a return type hint",
        "sample.py:25:Public.name: public callable lacks a return type hint",
        "sample.py:25:Public.name: public callable parameter 'value' lacks a type hint",
        "sample.py:28:Public.__str__: public callable lacks a return type hint",
        "sample.py:31:Public.__call__: public callable lacks a return type hint",
    ]


def test_auditor_ignores_nonpublic_nested_imported_and_lambda_callables() -> None:
    """Keep nonpublic and non-definition callables outside the public surface."""
    source = '''"""Documented module."""
from somewhere import imported

factory = lambda value: value

def public(value: str) -> str:
    """Return a value while defining private local behavior."""
    def nested(missing):
        return missing
    return value

def _private(missing):
    return missing
'''
    assert _audit_text(source) == []


def test_auditor_rejects_empty_or_invalid_forward_annotations() -> None:
    """Reject forward annotations that do not contain a type expression."""
    source = '''"""Documented module."""

def empty(value: "") -> None:
    """Accept one value."""

def whitespace(value: "   ") -> None:
    """Accept one value."""

def invalid(value: "list[") -> None:
    """Accept one value."""

def invalid_return(value: str) -> ")":
    """Return one value."""

def empty_return(value: str) -> "":
    """Return one value."""

def whitespace_return(value: str) -> "   ":
    """Return one value."""

def valid(value: " Model | None ") -> " list[Model] ":
    """Preserve valid forward references."""

def explicit_none(value: None) -> None:
    """Preserve the normal None annotation."""
'''
    assert _audit_text(source) == [
        "sample.py:3:empty: public callable parameter 'value' has an invalid type hint",
        "sample.py:6:whitespace: public callable parameter 'value' "
        "has an invalid type hint",
        "sample.py:9:invalid: public callable parameter 'value' "
        "has an invalid type hint",
        "sample.py:12:invalid_return: public callable has an invalid return type hint",
        "sample.py:15:empty_return: public callable has an invalid return type hint",
        "sample.py:18:whitespace_return: public callable has an invalid "
        "return type hint",
    ]


def test_auditor_types_every_direct_magic_method() -> None:
    """Treat every directly defined magic method as public protocol surface."""
    source = '''"""Documented module."""

class Protocol:
    """Provide representative protocol hooks."""

    def __bool__(self):
        return True

    def __future_protocol__(self, value):
        return value

    def _private(self, value):
        return value
'''
    assert _audit_text(source) == [
        "sample.py:6:Protocol.__bool__: public callable lacks a return type hint",
        "sample.py:9:Protocol.__future_protocol__: public callable lacks a "
        "return type hint",
        "sample.py:9:Protocol.__future_protocol__: public callable parameter 'value' "
        "lacks a type hint",
    ]


def test_auditor_rejects_non_type_root_annotations() -> None:
    """Reject literal, callable, container and comprehension root shapes."""
    invalid_annotations = (
        "42",
        'b"Model"',
        "True",
        "False",
        "...",
        "[]",
        "{}",
        "set()",
        "(int, str)",
        "lambda: int",
        "factory()",
        "factory().Model",
        "factory()[Model]",
        "Model | 42",
        "42 | Model",
        "Model + Other",
        "[item for item in items]",
        "{item for item in items}",
        "(item for item in items)",
        "{item: item for item in items}",
        '"42"',
        '"func()"',
    )
    for annotation in invalid_annotations:
        parameter_source = f'''"""Documented module."""

def public(value: {annotation}) -> None:
    """Accept one value."""
'''
        assert _audit_text(parameter_source) == [
            "sample.py:3:public: public callable parameter 'value' "
            "has an invalid type hint"
        ]

        return_source = f'''"""Documented module."""

def public(value: int) -> {annotation}:
    """Return one value."""
'''
        assert _audit_text(return_source) == [
            "sample.py:3:public: public callable has an invalid return type hint"
        ]


def test_auditor_accepts_supported_type_root_annotations() -> None:
    """Preserve references, generics, unions, metadata and explicit None."""
    for annotation in (
        "Model",
        "models.Model",
        "list[Model]",
        "Model | None",
        "Literal[42]",
        'Annotated[int, "meta"]',
        "None",
        '"Model | None"',
    ):
        source = f'''"""Documented module."""

def public(value: {annotation}) -> {annotation}:
    """Round-trip one value."""
'''
        assert _audit_text(source) == []
