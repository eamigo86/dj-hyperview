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


def _returns_value(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        annotation = ast.parse(annotation.value.strip(), mode="eval").body
    if isinstance(annotation, ast.Constant):
        return annotation.value is not None
    if isinstance(annotation, ast.Name):
        return annotation.id not in {"NoneType", "NoReturn", "Never"}
    if isinstance(annotation, ast.Attribute):
        return annotation.attr not in {"NoneType", "NoReturn", "Never"}
    return True


def _type_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    symbol: str,
    *,
    skip_bound_receiver: bool = False,
) -> list[str]:
    positional = [*node.args.posonlyargs, *node.args.args]
    receiver = positional[0] if skip_bound_receiver and positional else None
    parameters = [
        *positional,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg is not None else []),
        *([node.args.kwarg] if node.args.kwarg is not None else []),
    ]
    issues: list[str] = []
    for parameter in parameters:
        if parameter is receiver:
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


def _function_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    skip_bound_receiver: bool = False,
) -> list[str]:
    positional = [*node.args.posonlyargs, *node.args.args]
    if skip_bound_receiver and positional:
        positional = positional[1:]
    names = [
        *(parameter.arg for parameter in positional),
        *(parameter.arg for parameter in node.args.kwonlyargs),
    ]
    if node.args.vararg is not None:
        names.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg is not None:
        names.append(f"**{node.args.kwarg.arg}")
    return names


def _entry_separator(text: str) -> tuple[int | None, bool]:
    closing = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, character in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in closing:
            stack.append(closing[character])
        elif character in closing.values():
            if not stack or stack[-1] != character:
                return None, False
            stack.pop()
        elif character == ":" and not stack:
            return index, True
    return None, not stack and quote is None


def _argument_entry(line: str) -> tuple[str | None, bool]:
    if not line.startswith("    ") or line.startswith("     "):
        return None, False
    content = line[4:]
    separator, balanced = _entry_separator(content)
    if not balanced:
        return None, True
    if separator is None:
        return None, False
    declaration = content[:separator]
    label = declaration
    if label.endswith(")") and " (" in label:
        label = label.split(" (", 1)[0]
    stars = len(label) - len(label.lstrip("*"))
    name = label[stars:]
    if stars > 2 or not name.isidentifier():
        return None, True
    return label, True


def _function_args_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    symbol: str,
    docstring: str,
    *,
    skip_bound_receiver: bool = False,
) -> list[str]:
    expected = set(_function_parameters(node, skip_bound_receiver=skip_bound_receiver))
    lines = docstring.splitlines()
    headers = [index for index, line in enumerate(lines) if line == "Args:"]
    if not headers:
        if expected:
            return [_diagnostic(path, node, symbol, "Args section is required")]
        return []
    issues: list[str] = []
    if len(headers) > 1:
        issues.append(_diagnostic(path, node, symbol, "Args section is duplicated"))
    body: list[str] = []
    for line in lines[headers[0] + 1 :]:
        if line and not line.startswith(" "):
            break
        body.append(line)
    if not any(line.strip() for line in body):
        issues.append(_diagnostic(path, node, symbol, "Args section is empty"))
    documented: list[str] = []
    for line in body:
        label, is_entry = _argument_entry(line)
        if not is_entry:
            continue
        if label is None:
            issues.append(
                _diagnostic(
                    path,
                    node,
                    symbol,
                    "Args section has an invalid parameter label",
                )
            )
        else:
            documented.append(label)
            content = line[4:]
            separator, _ = _entry_separator(content)
            declaration = content[:separator] if separator is not None else content
            if declaration.endswith(")") and " (" in declaration:
                issues.append(
                    _diagnostic(
                        path,
                        node,
                        symbol,
                        f"Args section repeats the type for parameter '{label}'",
                    )
                )
    for name in sorted(set(documented)):
        if documented.count(name) > 1:
            issues.append(
                _diagnostic(
                    path,
                    node,
                    symbol,
                    f"Args section documents parameter '{name}' more than once",
                )
            )
    for name in sorted(set(documented) - expected):
        issues.append(
            _diagnostic(
                path,
                node,
                symbol,
                f"Args section documents unknown parameter '{name}'",
            )
        )
    for name in sorted(expected - set(documented)):
        issues.append(
            _diagnostic(path, node, symbol, f"Args section missing parameter '{name}'")
        )
    return issues


def _returns_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    symbol: str,
    docstring: str,
) -> list[str]:
    lines = docstring.splitlines()
    headers = [index for index, line in enumerate(lines) if line == "Returns:"]
    has_value = _returns_value(node.returns)
    if not headers:
        if has_value:
            return [_diagnostic(path, node, symbol, "Returns section is required")]
        return []
    if not has_value:
        return [
            _diagnostic(
                path,
                node,
                symbol,
                "Returns section is not allowed for a no-value return",
            )
        ]
    body: list[str] = []
    for line in lines[headers[0] + 1 :]:
        if line and not line.startswith(" "):
            break
        body.append(line)
    prose = next((line.strip() for line in body if line.strip()), None)
    if prose is None:
        return [_diagnostic(path, node, symbol, "Returns section is empty")]
    separator, balanced = _entry_separator(prose)
    if separator is not None and balanced:
        label = prose[:separator].strip()
        try:
            expression = ast.parse(label, mode="eval").body
        except SyntaxError:
            expression = None
        if expression is not None and _is_valid_type_hint(expression):
            return [
                _diagnostic(
                    path,
                    node,
                    symbol,
                    "Returns section repeats the return type",
                )
            ]
    return []


def _rest_type_issues(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    symbol: str,
    docstring: str,
) -> list[str]:
    stripped = [line.strip() for line in docstring.splitlines()]
    issues: list[str] = []
    if any(line.startswith(":type ") and ":" in line[6:] for line in stripped):
        issues.append(
            _diagnostic(
                path, node, symbol, "docstring repeats a parameter type with :type"
            )
        )
    if any(line.startswith(":rtype:") for line in stripped):
        issues.append(
            _diagnostic(
                path, node, symbol, "docstring repeats a return type with :rtype"
            )
        )
    return issues


def _has_bound_receiver(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        name = decorator.id if isinstance(decorator, ast.Name) else None
        if isinstance(decorator, ast.Attribute):
            name = decorator.attr
        if name == "staticmethod":
            return False
    return bool(node.args.posonlyargs or node.args.args)


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
            type_issues = _type_issues(node, path, node.name)
            issues.extend(type_issues)
            docstring = ast.get_docstring(node)
            if docstring is not None and not type_issues:
                issues.extend(_function_args_issues(node, path, node.name, docstring))
                issues.extend(_returns_issues(node, path, node.name, docstring))
                issues.extend(_rest_type_issues(node, path, node.name, docstring))
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
                    bound_receiver = _has_bound_receiver(method)
                    type_issues = _type_issues(
                        method,
                        path,
                        symbol,
                        skip_bound_receiver=bound_receiver,
                    )
                    issues.extend(type_issues)
                    docstring = ast.get_docstring(method)
                    if docstring is not None and not type_issues:
                        issues.extend(
                            _function_args_issues(
                                method,
                                path,
                                symbol,
                                docstring,
                                skip_bound_receiver=bound_receiver,
                            )
                        )
                        issues.extend(_returns_issues(method, path, symbol, docstring))
                        issues.extend(
                            _rest_type_issues(method, path, symbol, docstring)
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
    """Return the export collection.

    Returns:
        The current export collection.
    """
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
    """Return a value while defining private local behavior.

    Args:
        value: Value to return.

    Returns:
        The supplied value.
    """
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
    """Preserve valid forward references.

    Args:
        value: Forward-referenced value.

    Returns:
        Values preserving the forward reference.
    """

def explicit_none(value: None) -> None:
    """Preserve the normal None annotation.

    Args:
        value: Explicit none value.
    """
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
        returns = (
            ""
            if annotation == "None"
            else "\n    Returns:\n        The preserved value.\n"
        )
        source = f'''"""Documented module."""

def public(value: {annotation}) -> {annotation}:
    """Round-trip one value.

    Args:
        value: Value to preserve.
{returns}
    """
'''
        assert _audit_text(source) == []


def test_auditor_requires_nonempty_args_for_public_functions() -> None:
    """Reject missing and empty argument sections on module functions."""
    for docstring, reasons in (
        ('"""Process a value."""', ["Args section is required"]),
        (
            '"""Process a value.\n\n    Args:\n    """',
            ["Args section is empty", "Args section missing parameter 'value'"],
        ),
    ):
        source = f'''"""Documented module."""
def public(value: str) -> None:
    {docstring}
'''
        assert _audit_text(source) == [
            f"sample.py:2:public: {reason}" for reason in reasons
        ]


def test_auditor_requires_each_function_argument_exactly_once() -> None:
    """Reject missing, extra, and duplicate public function argument entries."""
    source = '''"""Documented module."""
def public(first: str, second: str) -> None:
    """Process values.

    Args:
        first: First value.
        first: Duplicate value.
        extra: Extra value.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section documents parameter 'first' more than once",
        "sample.py:2:public: Args section documents unknown parameter 'extra'",
        "sample.py:2:public: Args section missing parameter 'second'",
    ]


def test_auditor_requires_one_exact_args_header() -> None:
    """Reject duplicate and noncanonical argument section headers."""
    duplicate = '''"""Documented module."""
def public(value: str) -> None:
    """Process one value.

    Args:
        value: First entry.

    Args:
        value: Second entry.
    """
'''
    assert _audit_text(duplicate) == ["sample.py:2:public: Args section is duplicated"]
    wrong_header = '''"""Documented module."""
def public(value: str) -> None:
    """Process one value.

    Arguments:
        value: Value to process.
    """
'''
    assert _audit_text(wrong_header) == ["sample.py:2:public: Args section is required"]


def test_auditor_normalizes_all_sync_and_async_parameter_kinds() -> None:
    """Accept exact entries for positional, keyword, and variadic arguments."""
    source = '''"""Documented module."""
async def public(pos_only: str, /, regular: str, *items: str,
                 keyword_only: bool, **options: str) -> None:
    """Process every argument category.

    Args:
        pos_only: Positional-only value.
        regular: Regular value.
        *items: Additional values.
        keyword_only: Keyword-only flag.
        **options: Additional options.
    """
'''
    assert _audit_text(source) == []


def test_module_self_and_cls_are_ordinary_public_parameters() -> None:
    """Prevent module self and cls names from evading docs or type hints."""
    documented = '''"""Documented module."""
def public(self: str, cls: str) -> None:
    """Process ordinary parameters.

    Args:
        self: First value.
    """
'''
    assert _audit_text(documented) == [
        "sample.py:2:public: Args section missing parameter 'cls'"
    ]
    assert _audit_text('''"""Documented module."""
def public(self, cls) -> None:
    """Process ordinary parameters."""
''') == [
        "sample.py:2:public: public callable parameter 'cls' lacks a type hint",
        "sample.py:2:public: public callable parameter 'self' lacks a type hint",
    ]


def test_args_section_rejects_entries_when_function_has_no_parameters() -> None:
    """Reject argument documentation that has no matching parameter."""
    source = '''"""Documented module."""
def public() -> None:
    """Perform work.

    Args:
        extra: Unsupported value.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section documents unknown parameter 'extra'"
    ]


def test_args_section_preserves_variadic_stars() -> None:
    """Require exact identities for regular and variadic parameters."""
    source = '''"""Documented module."""
def variadic(*items: str, **options: str) -> None:
    """Process variadic values.

    Args:
        items: Missing the positional variadic marker.
        options: Missing the keyword variadic marker.
    """

def regular(value: str) -> None:
    """Process one regular value.

    Args:
        *value: Unexpected variadic marker.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:variadic: Args section documents unknown parameter 'items'",
        "sample.py:2:variadic: Args section documents unknown parameter 'options'",
        "sample.py:2:variadic: Args section missing parameter '**options'",
        "sample.py:2:variadic: Args section missing parameter '*items'",
        "sample.py:10:regular: Args section documents unknown parameter '*value'",
        "sample.py:10:regular: Args section missing parameter 'value'",
    ]


def test_args_section_accepts_unicode_identifiers_and_continuations() -> None:
    """Accept Python identifiers without treating continuation text as entries."""
    source = '''"""Documented module."""
def public(café: str, 变量: str, *éléments: str) -> None:
    """Process Unicode-named parameters.

    Args:
        café: First value.
            Continuation: remains descriptive text.
        变量: Second value.
        *éléments: Additional values.
    """
'''
    assert _audit_text(source) == []


def test_args_section_rejects_non_identifier_labels() -> None:
    """Reject an entry whose label is not a Python identifier."""
    source = '''"""Documented module."""
def public(value: str) -> None:
    """Process one value.

    Args:
        bad-name: Invalid label.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section has an invalid parameter label",
        "sample.py:2:public: Args section missing parameter 'value'",
    ]


def test_args_section_parses_colons_inside_temporary_type_suffixes() -> None:
    """Ignore nested and quoted colons while locating an entry separator."""
    source = r'''"""Documented module."""
def public(literal: str, annotated: str, escaped: str) -> None:
    r"""Process typed argument entries.

    Args:
        literal (Literal["x:y"]): Description may contain ): safely.
        annotated (Annotated[list[dict[str, tuple[int, ...]]], "x:y"]): Nested.
        escaped (Literal["x\":y"]): Escaped quote.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section repeats the type for parameter 'annotated'",
        "sample.py:2:public: Args section repeats the type for parameter 'escaped'",
        "sample.py:2:public: Args section repeats the type for parameter 'literal'",
    ]


def test_args_section_parses_colons_inside_balanced_mapping_metadata() -> None:
    """Keep mapping metadata colons inside the temporary type suffix."""
    source = r'''"""Documented module."""
def public(mapping: str) -> None:
    r"""Process mapping metadata.

    Args:
        mapping (Annotated[str, {"key:part": {"nested:key": "value"}}]): Value.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section repeats the type for parameter 'mapping'"
    ]


def test_args_section_parses_escaped_single_quoted_metadata() -> None:
    """Keep an escaped quote and colon inside single-quoted metadata."""
    source = r'''"""Documented module."""
def public(value: str) -> None:
    r"""Process quoted metadata.

    Args:
        value (Literal['x\':y']): Value whose description contains ): safely.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:public: Args section repeats the type for parameter 'value'"
    ]


def test_args_section_rejects_unbalanced_temporary_type_suffixes() -> None:
    """Diagnose malformed suffix structure without crashing the audit."""
    for entry in (
        'value (Literal["x:y"]: Missing parenthesis.',
        'value (Literal["x:y]): Missing quote.',
        "value (dict[str, list[int]]}: Mismatched delimiter.",
    ):
        source = f'''"""Documented module."""
def public(value: str) -> None:
    """Process one value.

    Args:
        {entry}
    """
'''
        assert _audit_text(source) == [
            "sample.py:2:public: Args section has an invalid parameter label",
            "sample.py:2:public: Args section missing parameter 'value'",
        ]


def test_method_args_share_bound_receiver_semantics_with_typing() -> None:
    """Exclude renamed instance and class receivers but not static parameters."""
    source = '''"""Documented module."""
class Public:
    """Provide representative method bindings."""

    def run(receiver, value: str) -> None:
        """Run with one value.

        Args:
            value: Value to process.
        """

    @classmethod
    def build(receiver, value: str) -> None:
        """Build with one value.

        Args:
            value: Value to process.
        """

    @staticmethod
    def inspect(self: str) -> None:
        """Inspect an ordinary static parameter."""
'''
    assert _audit_text(source) == [
        "sample.py:21:Public.inspect: Args section is required"
    ]


def test_method_args_cover_properties_and_implicit_class_bindings() -> None:
    """Recognize property, constructor, protocol, and implicit class receivers."""
    source = '''"""Documented module."""
class Public:
    """Provide representative descriptors and protocol hooks."""

    @property
    def name(receiver) -> str:
        """Return the current name.

        Returns:
            The current name.
        """
        return "name"

    @name.setter
    def name(receiver, value: str) -> None:
        """Set the current name.

        Args:
            value: New name.
        """

    def __new__(receiver, value: str) -> "Public":
        """Create an instance.

        Args:
            value: Initial value.

        Returns:
            The new instance.
        """
        return super().__new__(receiver)

    def __bool__(receiver) -> bool:
        """Return whether the instance is truthy.

        Returns:
            Whether the instance is truthy.
        """
        return True

    def __init_subclass__(receiver, flag: bool = False) -> None:
        """Initialize one subclass.

        Args:
            flag: Optional subclass flag.
        """

    def __class_getitem__(receiver, item: str) -> str:
        """Resolve one class item.

        Args:
            item: Item to resolve.

        Returns:
            The resolved item.
        """
        return item
'''
    assert _audit_text(source) == []


def test_method_args_reject_unknown_entries_without_parameters() -> None:
    """Audit present argument sections even when a method has no parameters."""
    source = '''"""Documented module."""
class Public:
    """Provide a malformed method contract."""

    @staticmethod
    def ping() -> None:
        """Perform work.

        Args:
            extra: Unsupported value.
        """
'''
    assert _audit_text(source) == [
        "sample.py:6:Public.ping: Args section documents unknown parameter 'extra'"
    ]


def test_method_args_preserve_all_parameter_and_parser_semantics() -> None:
    """Apply exact variadic, Unicode, and nested suffix parsing to methods."""
    source = r'''"""Documented module."""
class Public:
    """Provide one fully shaped method."""

    def run(receiver, café: str, /, regular: str, *items: str,
            变量: bool, **options: str) -> None:
        r"""Run with every parameter category.

        Args:
            café: Positional-only value.
            regular (Annotated[str, {"key:part": "value"}]): Regular value.
            *items: Additional values.
            变量: Keyword-only value.
            **options: Additional options.
        """
'''
    assert _audit_text(source) == [
        "sample.py:5:Public.run: Args section repeats the type for parameter 'regular'"
    ]


def test_method_args_cover_async_binding_variants() -> None:
    """Apply receiver semantics consistently to async method variants."""
    source = '''"""Documented module."""
class Public:
    """Provide asynchronous method bindings."""

    async def run(receiver, value: str) -> None:
        """Run asynchronously.

        Args:
            value: Value to process.
        """

    @classmethod
    async def build(receiver, value: str) -> None:
        """Build asynchronously.

        Args:
            value: Value to process.
        """

    @staticmethod
    async def inspect(self: str) -> None:
        """Inspect an ordinary static parameter.

        Args:
            self: Value to inspect.
        """
'''
    assert _audit_text(source) == []


def test_method_args_cover_property_deleters() -> None:
    """Treat a property deleter's first positional parameter as its receiver."""
    source = '''"""Documented module."""
class Public:
    """Provide a deletable property."""

    @property
    def name(receiver) -> str:
        """Return the current name.

        Returns:
            The current name.
        """
        return "name"

    @name.deleter
    def name(receiver) -> None:
        """Delete the current name."""
'''
    assert _audit_text(source) == []


def test_method_args_cover_attributed_staticmethod_decorator_orders() -> None:
    """Recognize attributed staticmethod decorators in either decorator order."""
    source = '''"""Documented module."""
import builtins

def _trace(value):
    return value

class Public:
    """Provide decorated static methods."""

    @_trace
    @builtins.staticmethod
    def first(self: str) -> None:
        """Inspect the first value.

        Args:
            self: First ordinary static parameter.
        """

    @builtins.staticmethod
    @_trace
    def second(cls: str) -> None:
        """Inspect the second value.

        Args:
            cls: Second ordinary static parameter.
        """
'''
    assert _audit_text(source) == []


def test_returns_sections_match_value_semantics() -> None:
    """Require returns prose only for annotations that can produce a value."""
    source = '''"""Documented module."""
def value() -> str:
    """Return one value."""

def empty() -> None:
    """Return no value.

    Returns:
        Unsupported result.
    """

def none_type() -> NoneType:
    """Return no value."""

def no_return() -> NoReturn:
    """Never return."""

def never() -> Never:
    """Never return."""

def optional() -> str | None:
    """Return an optional value."""
'''
    assert _audit_text(source) == [
        "sample.py:2:value: Returns section is required",
        "sample.py:5:empty: Returns section is not allowed for a no-value return",
        "sample.py:21:optional: Returns section is required",
    ]


def test_returns_sections_cover_async_properties_and_magic_methods() -> None:
    """Audit value returns on async, property, and magic callables."""
    source = '''"""Documented module."""
async def fetch() -> str:
    """Fetch one value."""

class Public:
    """Provide value-returning methods."""

    @property
    def name(receiver) -> str:
        """Return the current name."""

    def __str__(receiver) -> str:
        """Return a display value.

        Returns:
            Human-readable value.
        """
        return "value"
'''
    assert _audit_text(source) == [
        "sample.py:2:fetch: Returns section is required",
        "sample.py:9:Public.name: Returns section is required",
    ]


def test_returns_section_rejects_empty_and_typed_entries() -> None:
    """Reject empty returns prose and labels that repeat the return type."""
    source = '''"""Documented module."""
def empty() -> str:
    """Return one value.

    Returns:
    """

def typed() -> list[str]:
    """Return several values.

    Returns:
        list[str]: Collected values.
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:empty: Returns section is empty",
        "sample.py:8:typed: Returns section repeats the return type",
    ]


def test_public_docstrings_reject_explicit_type_repetition() -> None:
    """Reject Google and reStructuredText forms that duplicate annotations."""
    source = '''"""Documented module."""
def google(value: str) -> None:
    """Process one value.

    Args:
        value (str): Value to process.
    """

def rest(value: str) -> str:
    """Return one value.

    Args:
        value: Value to process.

    Returns:
        Processed value.

    :type value: str
    :rtype: str
    """
'''
    assert _audit_text(source) == [
        "sample.py:2:google: Args section repeats the type for parameter 'value'",
        "sample.py:9:rest: docstring repeats a parameter type with :type",
        "sample.py:9:rest: docstring repeats a return type with :rtype",
    ]


def test_returns_prose_does_not_confuse_raises_entries() -> None:
    """Accept direct returns prose and exception labels in a Raises section."""
    source = '''"""Documented module."""
def public(value: str) -> str:
    """Return a checked value.

    Args:
        value: Value to check.

    Returns:
        The checked value.

    Raises:
        ValueError: If the value is empty.
    """
    if not value:
        raise ValueError
    return value
'''
    assert _audit_text(source) == []
