"""Rules A and B of the Python contract, enforced with the ast module.

Rule A: every public module, class, function and method has a complete Google
docstring. Rule B: every function annotates its parameters and return, and no
docstring repeats a type. Rule C (backticks) is tested elsewhere.

Public means every module, including underscore and package init modules, plus
each name without a leading underscore or listed in __all__, plus the
non-private members of a public class. Overloads and property setters and
deleters are skipped for rule A. Rule B covers every function at any nesting.

The scan reads source with the ast module and never imports it. Known limits:
a heading is recognised only when it sits alone on its line; section entries
are found by indentation, so a continuation line indented like an entry can be
miscounted; a raised exception counts only when the raise names a class
directly (a bare re-raise or a raise of a variable is ignored, and so is a raise
inside a try whose handler catches that name in the same function); Returns is
not required when the function has no return annotation and no value return,
and is never required for a property or a generator; an inherited or
decorator-provided signature is not followed.

SCOPE lists the folders (or single files) under test, relative to the repository
root, so adding one entry extends the rules to it.
"""

import ast
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
SCOPE = ("src/dj_hyperview", "tests")
SKIPPED_PARTS = frozenset({"migrations", "__pycache__"})

Finding = tuple[int, str, str]

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
TRYS = (ast.Try, ast.TryStar)
CATCHING_CONTEXTS = frozenset(
    {"raises", "assertRaises", "assertRaisesRegex", "suppress"}
)
Function = ast.FunctionDef | ast.AsyncFunctionDef
Param = tuple[str, ast.expr | None, str]

HEADINGS = [
    "Args",
    "Returns",
    "Yields",
    "Raises",
    "Attributes",
    "Example",
    "Examples",
    "Note",
    "Notes",
    "Warning",
    "Todo",
    "See Also",
]
CANON = {name.lower(): name for name in HEADINGS}
CANON.update(
    {
        "arguments": "Args",
        "parameters": "Args",
        "params": "Args",
        "param": "Args",
        "keyword args": "Args",
        "keyword arguments": "Args",
        "kwargs": "Args",
        "return": "Returns",
        "yield": "Yields",
        "raise": "Raises",
        "attribute": "Attributes",
        "warnings": "Warning",
    }
)
HEAD = re.compile(r"^([A-Za-z][A-Za-z ]*?):\s*$")
ENTRY = re.compile(r"^(\*{0,2}\w+(?:\s*,\s*\*{0,2}\w+)*)\s*(?:\((.+?)\))?\s*:(?:\s|$)")


@dataclass
class Unit:
    """One documented unit found in a module."""

    kind: str
    node: ast.AST
    name: str
    public: bool
    line: int


def parse_sections(doc: str) -> list[dict]:
    """Split a cleaned docstring into its recognised sections.

    Args:
        doc: docstring text.

    Returns:
        One dict per heading with its heading, canonical name and body lines.
    """
    sections: list[dict] = []
    current: dict | None = None
    for line in doc.split("\n"):
        match = HEAD.match(line)
        if match and match.group(1).lower() in CANON:
            current = {
                "heading": match.group(1),
                "canon": CANON[match.group(1).lower()],
                "lines": [],
            }
            sections.append(current)
        elif current is not None:
            current["lines"].append(line)
    return sections


def entries(lines: list[str]) -> list[re.Match[str]]:
    """Find the entries written at the indentation of the first line.

    Args:
        lines: body lines of one section.

    Returns:
        A match per entry line.
    """
    filled = [line for line in lines if line.strip()]
    if not filled:
        return []
    base = len(filled[0]) - len(filled[0].lstrip())
    found = []
    for line in filled:
        if len(line) - len(line.lstrip()) == base:
            match = ENTRY.match(line.strip())
            if match:
                found.append(match)
    return found


def entry_names(lines: list[str]) -> set[str]:
    """Collect the parameter names that a section documents.

    Args:
        lines: body lines of one Args section.

    Returns:
        The names without their star prefixes.
    """
    names: set[str] = set()
    for match in entries(lines):
        names.update(part.strip().lstrip("*") for part in match.group(1).split(","))
    return names


def looks_typed(line: str) -> bool:
    """Tell whether a line starts with a type followed by a colon.

    Args:
        line: first line of a Returns or Yields section.

    Returns:
        True when the text before the first top-level colon reads as a type.
    """
    line = line.strip()
    depth, index = 0, -1
    for position, char in enumerate(line):
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        elif char == ":" and depth == 0:
            index = position
            break
    if index <= 0:
        return False
    prefix = line[:index]
    outside, level = "", 0
    for char in prefix:
        if char == "[":
            level += 1
        elif char == "]":
            level -= 1
        elif level == 0:
            outside += char
    pattern = r"[\w.]+(\s*\|\s*[\w.]+)*"
    return bool(re.fullmatch(pattern, outside.strip())) and prefix.count(
        "["
    ) == prefix.count("]")


def own_nodes(function: Function) -> Iterator[ast.AST]:
    """Walk a function body without entering nested functions or classes.

    Args:
        function: the function to walk.

    Yields:
        Each node that belongs to the function itself.
    """
    stack = [c for c in function.body if not isinstance(c, (*FUNCS, ast.ClassDef))]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (*FUNCS, ast.Lambda, ast.ClassDef)):
                stack.append(child)


def decorator_names(function: Function) -> list[str]:
    """List the final name of each decorator.

    Args:
        function: the decorated function.

    Returns:
        Names such as property or setter, calls reduced to their callee.
    """
    names = []
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call):
            decorator = decorator.func
        if isinstance(decorator, ast.Name):
            names.append(decorator.id)
        elif isinstance(decorator, ast.Attribute):
            names.append(decorator.attr)
    return names


def returns_nothing(annotation: ast.expr) -> bool:
    """Tell whether a return annotation means no returned value.

    Args:
        annotation: the return annotation.

    Returns:
        True for None, NoReturn and Never.
    """
    if isinstance(annotation, ast.Constant):
        return annotation.value is None or annotation.value in (
            "None",
            "NoReturn",
            "Never",
        )
    if isinstance(annotation, ast.Name):
        return annotation.id in ("NoReturn", "Never")
    if isinstance(annotation, ast.Attribute):
        return annotation.attr in ("NoReturn", "Never")
    return False


def all_params(function: Function) -> list[Param]:
    """List every parameter with its annotation and star prefix.

    Args:
        function: the function to inspect.

    Returns:
        Tuples of name, annotation node and prefix, in signature order.
    """
    args = function.args
    params: list[Param] = [
        (a.arg, a.annotation, "") for a in args.posonlyargs + args.args
    ]
    if args.vararg:
        params.append((args.vararg.arg, args.vararg.annotation, "*"))
    params += [(a.arg, a.annotation, "") for a in args.kwonlyargs]
    if args.kwarg:
        params.append((args.kwarg.arg, args.kwarg.annotation, "**"))
    return params


def exported_names(tree: ast.Module) -> set[str]:
    """Read the literal names assigned to __all__.

    Args:
        tree: the parsed module.

    Returns:
        The exported names, empty when __all__ is absent or not a literal.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, (ast.AnnAssign, ast.AugAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            value = node.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            names.update(
                e.value
                for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
    return names


def flatten(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements, entering compound statements but not definitions.

    Args:
        body: statements to walk.

    Yields:
        Each statement, nested ones included.
    """
    for statement in body:
        yield statement
        if isinstance(
            statement,
            (ast.If, ast.For, ast.While, ast.With, ast.AsyncWith, ast.AsyncFor, *TRYS),
        ):
            groups = [
                statement.body,
                getattr(statement, "orelse", []),
                getattr(statement, "finalbody", []),
            ]
            groups += [h.body for h in getattr(statement, "handlers", [])]
            for group in groups:
                yield from flatten(group)


def collect(tree: ast.Module, path: str) -> list[Unit]:
    """Find every module, class, function and method with its publicity.

    Args:
        tree: the parsed module.
        path: repository path used as the module name.

    Returns:
        The units in source order, nested definitions included.
    """
    units = [Unit("module", tree, path, True, 1)]
    exported = exported_names(tree)

    def visit(
        body: list[ast.stmt], context: str, qual: str, class_public: bool
    ) -> None:
        for statement in flatten(body):
            if not isinstance(statement, (*FUNCS, ast.ClassDef)):
                continue
            is_class = isinstance(statement, ast.ClassDef)
            if context == "module":
                public = (
                    not statement.name.startswith("_") or statement.name in exported
                )
            elif context == "class":
                public = class_public and not statement.name.startswith("_")
            else:
                public = False
            if is_class:
                kind = "class"
            else:
                kind = "method" if context == "class" else "function"
            name = qual + statement.name
            units.append(Unit(kind, statement, name, public, statement.lineno))
            if is_class:
                visit(statement.body, "class", name + ".", public)
            else:
                visit(nested_definitions(statement), "func", name + ".", False)

    visit(tree.body, "module", "", True)
    return units


def nested_definitions(function: Function) -> list[ast.stmt]:
    """Find definitions anywhere in a body, outside deeper definitions.

    Args:
        function: the function to search.

    Returns:
        Nested functions and classes ordered by line.
    """
    stack: list[ast.AST] = list(function.body)
    found: list[ast.stmt] = []
    while stack:
        node = stack.pop(0)
        if isinstance(node, (*FUNCS, ast.ClassDef)):
            found.append(node)
        else:
            stack.extend(
                c for c in ast.iter_child_nodes(node) if isinstance(c, ast.stmt)
            )
    return sorted(found, key=lambda n: n.lineno)


def handler_names(node: ast.Try | ast.TryStar) -> set[str]:
    """List the exception names that a try statement catches.

    Args:
        node: the try statement.

    Returns:
        Names, with a star for a bare except.
    """
    names: set[str] = set()
    for handler in node.handlers:
        if handler.type is None:
            names.add("*")
            continue
        types = (
            handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        )
        for item in types:
            names.add(
                item.id
                if isinstance(item, ast.Name)
                else item.attr
                if isinstance(item, ast.Attribute)
                else "?"
            )
    return names


def context_catches(node: ast.With | ast.AsyncWith) -> set[str]:
    """List the exception names that a with statement's context managers absorb.

    Args:
        node: the with statement.

    Returns:
        Names given to pytest.raises, assertRaises, assertRaisesRegex or
        contextlib.suppress.
    """
    names: set[str] = set()
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        func_name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if func_name not in CATCHING_CONTEXTS:
            continue
        arguments = call.args if func_name == "suppress" else call.args[:1]
        for argument in arguments:
            for kind in (
                argument.elts if isinstance(argument, ast.Tuple) else [argument]
            ):
                names.add(
                    kind.id
                    if isinstance(kind, ast.Name)
                    else kind.attr
                    if isinstance(kind, ast.Attribute)
                    else "?"
                )
    return names


def own_raises(function: Function) -> list[str]:
    """Find exceptions raised by name and not caught inside the function.

    Args:
        function: the function to inspect.

    Returns:
        Exception names in source order, repeats included.
    """
    found: list[str] = []

    def walk(node: ast.AST, caught: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (*FUNCS, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Raise) and child.exc is not None:
                target = (
                    child.exc.func if isinstance(child.exc, ast.Call) else child.exc
                )
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if (
                    name
                    and name[0].isupper()
                    and not (caught & {name, "*", "Exception", "BaseException"})
                ):
                    found.append(name)
            if isinstance(child, TRYS):
                inner = caught | handler_names(child)
                for statement in child.body:
                    walk(ast.Module(body=[statement], type_ignores=[]), inner)
                rest = [s for h in child.handlers for s in h.body]
                for statement in rest + child.orelse + child.finalbody:
                    walk(ast.Module(body=[statement], type_ignores=[]), caught)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                inner = caught | context_catches(child)
                for statement in child.body:
                    walk(ast.Module(body=[statement], type_ignores=[]), inner)
            else:
                walk(child, caught)

    walk(function, frozenset())
    return found


def check_unit(unit: Unit, path: str) -> list[tuple[str, str]]:
    """Check one unit against rules A and B.

    Args:
        unit: the unit to check.
        path: repository path of its module.

    Returns:
        Pairs of kind and detail, empty when the unit complies.
    """
    node, kind = unit.node, unit.kind
    found: list[tuple[str, str]] = []
    doc = ast.get_docstring(node, clean=True)
    is_function = isinstance(node, FUNCS)
    applies = unit.public
    if isinstance(node, FUNCS):
        decorators = decorator_names(node)
        if {"overload", "setter", "deleter"} & set(decorators):
            applies = False
        skip_first = kind == "method" and "staticmethod" not in decorators
        first = (node.args.posonlyargs + node.args.args)[:1]
        for name, annotation, star in all_params(node):
            if skip_first and first and name == first[0].arg:
                continue
            if annotation is None:
                found.append(("param_annotation_missing", f"parameter {star}{name}"))
        if node.returns is None:
            found.append(("return_annotation_missing", node.name))
    sections = parse_sections(doc) if doc else []
    for section in sections:
        if section["canon"] == "Args":
            found.extend(
                ("type_in_args", f"{m.group(1)} ({m.group(2)})")
                for m in entries(section["lines"])
                if m.group(2)
            )
        if section["canon"] in ("Returns", "Yields"):
            first_line = next((x for x in section["lines"] if x.strip()), None)
            if first_line and looks_typed(first_line):
                found.append(
                    (
                        "type_in_returns",
                        f"{section['canon']}: {first_line.strip()[:50]}",
                    )
                )
    if not applies:
        return found
    if kind == "module":
        if not doc or not doc.strip():
            stem = path.rsplit("/", 1)[-1][:-3]
            which = "underscore" if stem.startswith("_") else "regular"
            found.append((f"module_docstring_missing_{which}", path))
            return found
    elif not doc or not doc.strip():
        found.append(("missing_docstring", unit.name))
        return found
    for section in sections:
        if section["heading"] not in CANON.values():
            found.append(
                (
                    "inexact_heading",
                    f"'{section['heading']}:' should be '{section['canon']}:'",
                )
            )
        if not any(x.strip() for x in section["lines"]):
            found.append(("empty_section", f"{section['heading']}:"))
    by_name = defaultdict(list)
    for section in sections:
        by_name[section["canon"]].append(section)
    documented = {n for s in by_name.get("Args", []) for n in entry_names(s["lines"])}
    if isinstance(node, ast.ClassDef):
        init = next(
            (n for n in node.body if isinstance(n, FUNCS) and n.name == "__init__"),
            None,
        )
        if init is not None:
            params = all_params(init)
            if init.args.posonlyargs + init.args.args:
                params = params[1:]
            init_doc = ast.get_docstring(init, clean=True)
            for section in parse_sections(init_doc) if init_doc else []:
                if section["canon"] == "Args":
                    documented |= entry_names(section["lines"])
            found.extend(
                ("ctor_args_undocumented", f"{star}{name}")
                for name, _, star in params
                if name not in documented
            )
        return found
    if not is_function or not isinstance(node, FUNCS):
        return found
    decorators = decorator_names(node)
    skip_first = kind == "method" and "staticmethod" not in decorators
    first = (node.args.posonlyargs + node.args.args)[:1]
    params = [
        p
        for p in all_params(node)
        if not (skip_first and first and p[0] == first[0].arg)
    ]
    real = {p[0] for p in params}
    if params and "Args" not in by_name:
        found.append(("missing_args_section", ", ".join(p[2] + p[0] for p in params)))
    else:
        found.extend(
            ("missing_param", f"{star}{name}")
            for name, _, star in params
            if name not in documented
        )
    found.extend(("extra_args_entry", n) for n in sorted(documented - real))
    own = list(own_nodes(node))
    has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own)
    is_property = bool({"property", "cached_property"} & set(decorators))
    if node.returns is not None:
        needs_returns = not returns_nothing(node.returns)
    else:
        needs_returns = any(
            isinstance(n, ast.Return)
            and n.value is not None
            and not (isinstance(n.value, ast.Constant) and n.value.value is None)
            for n in own
        )
    if needs_returns and not is_property and not has_yield and "Returns" not in by_name:
        found.append(("missing_returns", node.name))
    if has_yield and "Yields" not in by_name:
        found.append(("missing_yields", node.name))
    raised = list(dict.fromkeys(own_raises(node)))
    if raised and "Raises" not in by_name:
        found.append(("missing_raises", ", ".join(raised)))
    elif raised:
        text = " ".join(x for s in by_name["Raises"] for x in s["lines"])
        found.extend(
            ("incomplete_raises", name)
            for name in raised
            if not re.search(r"\b" + re.escape(name) + r"\b", text)
        )
    return found


def find_violations(source: str, path: str) -> list[Finding]:
    """Scan one module for violations of rules A and B.

    Args:
        source: Python source text.
        path: repository path to report and to judge the module name by.

    Returns:
        Findings as line, kind and detail, in source order.

    Raises:
        SyntaxError: when the source does not parse.
    """
    found: list[Finding] = []
    for unit in collect(ast.parse(source), path):
        found.extend(
            (unit.line, kind, f"{unit.name}: {detail}")
            for kind, detail in check_unit(unit, path)
        )
    return found


GOOD = '''"""Module doc."""
from typing import Iterator


def f(a: int, /, b: str, *args: int, c: int = 1, **kw: str) -> int:
    """Do it.

    Args:
        a: first.
        b: second.
        *args: more.
        c: kw only.
        **kw: extras.

    Returns:
        The value.

    Raises:
        ValueError: if bad.
    """
    if a:
        raise ValueError("x")
    return 1


def g(n: int) -> Iterator[int]:
    """Gen.

    Args:
        n: count.

    Yields:
        Numbers.
    """
    yield n


class K:
    """Klass.

    Args:
        x: value.
    """

    def __init__(self, x: int) -> None:
        self.x = x

    @property
    def p(self) -> int:
        """Prop."""
        return self.x

    @p.setter
    def p(self, v: int) -> None:
        self.x = v

    def m(self) -> None:
        """Method."""

    @staticmethod
    def s(a: int) -> int:
        """Static.

        Args:
            a: val.

        Returns:
            Same.
        """
        return a
'''

D = '"""Mod."""\n'

REPORTED = {
    "module_docstring_missing_regular": (
        "def f() -> None:\n    '''x.'''\n",
        "m.py",
    ),
    "module_docstring_missing_underscore": ("x = 1\n", "pkg/__init__.py"),
    "missing_docstring": (D + "def f() -> None:\n    pass\n", "m.py"),
    "missing_args_section": (
        D + "def f(a: int) -> None:\n    '''x.'''\n",
        "m.py",
    ),
    "missing_param": (
        D
        + "def f(a: int, b: int) -> None:\n"
        + "    '''x.\n\n    Args:\n        a: v.\n    '''\n",
        "m.py",
    ),
    "extra_args_entry": (
        D
        + "def f(a: int) -> None:\n"
        + "    '''x.\n\n    Args:\n        a: v.\n        z: v.\n    '''\n",
        "m.py",
    ),
    "missing_returns": (D + "def f() -> int:\n    '''x.'''\n    return 1\n", "m.py"),
    "missing_yields": (D + "def f() -> None:\n    '''x.'''\n    yield 1\n", "m.py"),
    "missing_raises": (
        D + "def f() -> None:\n    '''x.'''\n    raise ValueError('a')\n",
        "m.py",
    ),
    "incomplete_raises": (
        D
        + "def f() -> None:\n"
        + "    '''x.\n\n    Raises:\n        KeyError: a.\n    '''\n"
        + "    raise m.ValueError\n",
        "m.py",
    ),
    "inexact_heading": (
        D
        + "def f(a: int) -> None:\n"
        + "    '''x.\n\n    Arguments:\n        a: v.\n    '''\n",
        "m.py",
    ),
    "empty_section": (
        D
        + "def f() -> None:\n"
        + "    '''x.\n\n    Note:\n\n    Example:\n        y\n    '''\n",
        "m.py",
    ),
    "ctor_args_undocumented": (
        D
        + "class K:\n    '''x.'''\n"
        + "    def __init__(self, a: int) -> None:\n        pass\n",
        "m.py",
    ),
    "param_annotation_missing": (D + "def _f(a) -> None:\n    pass\n", "m.py"),
    "return_annotation_missing": (D + "def _f(a: int):\n    pass\n", "m.py"),
    "type_in_args": (
        D
        + "def f(a: int) -> None:\n"
        + "    '''x.\n\n    Args:\n        a (int): v.\n    '''\n",
        "m.py",
    ),
    "type_in_returns": (
        D
        + "def f() -> list[str]:\n"
        + "    '''x.\n\n    Returns:\n        list[str]: v.\n    '''\n"
        + "    return []\n",
        "m.py",
    ),
    "exported_private_name": (
        D + "__all__ = ['_f']\ndef _f() -> None:\n    pass\n",
        "m.py",
    ),
    "uncaught_raise_in_handler": (
        D
        + "def f() -> None:\n    '''x.'''\n    try:\n        pass\n"
        + "    except KeyError:\n        raise ValueError\n",
        "m.py",
    ),
}
EXPECTED_KIND = {
    "exported_private_name": "missing_docstring",
    "uncaught_raise_in_handler": "missing_raises",
}

QUIET = {
    "private function": D + "def _f() -> None:\n    pass\n",
    "private and dunder members": (
        D
        + "class K:\n    '''x.'''\n"
        + "    def _m(self) -> None:\n        pass\n"
        + "    def __x__(self) -> None:\n        pass\n"
    ),
    "nested function": (
        D + "def f() -> None:\n    '''x.'''\n    def inner() -> None:\n        pass\n"
    ),
    "raise inside a nested function": (
        D
        + "def f() -> None:\n    '''x.'''\n"
        + "    def inner() -> None:\n        raise ValueError\n"
    ),
    "raise caught in the same function": (
        D
        + "def f() -> None:\n    '''x.'''\n    try:\n        raise ValueError\n"
        + "    except ValueError:\n        pass\n"
    ),
    "raise expected by pytest.raises": (
        D
        + "import pytest\n"
        + "def f() -> None:\n    '''x.'''\n"
        + "    with pytest.raises(ValueError):\n        raise ValueError\n"
    ),
    "raise expected by assertRaises": (
        D
        + "class K:\n    '''x.'''\n"
        + "    def m(self) -> None:\n        '''x.'''\n"
        + "        with self.assertRaises((KeyError, ValueError)):\n"
        + "            raise ValueError\n"
    ),
    "raise suppressed by contextlib.suppress": (
        D
        + "from contextlib import suppress\n"
        + "def f() -> None:\n    '''x.'''\n"
        + "    with suppress(KeyError, ValueError):\n        raise ValueError\n"
    ),
    "overload": D
    + "from typing import overload\n@overload\ndef f(a: int) -> int: ...\n",
}


def test_a_context_manager_absorbs_only_the_exceptions_it_names() -> None:
    """Report a raise that the surrounding pytest.raises does not expect."""
    source = (
        D
        + "import pytest\n"
        + "def f() -> None:\n    '''x.'''\n"
        + "    with pytest.raises(KeyError):\n        raise ValueError\n"
    )

    assert "missing_raises" in kinds(source)


def kinds(source: str, path: str = "m.py") -> set[str]:
    """Report the finding kinds for a source text.

    Args:
        source: Python source to scan.
        path: repository path to report it under.

    Returns:
        The set of finding kinds, empty when the source complies.
    """
    return {kind for _, kind, _ in find_violations(source, path)}


def test_compliant_sample_has_no_findings() -> None:
    """Accept a sample that follows every rule."""
    assert find_violations(GOOD, "m.py") == []


@pytest.mark.parametrize("case", sorted(REPORTED))
def test_each_violation_kind_is_reported(case: str) -> None:
    """Report every kind of violation the scan knows.

    Args:
        case: name of the sample in REPORTED, also the expected kind unless
            EXPECTED_KIND names another one.
    """
    source, path = REPORTED[case]

    assert EXPECTED_KIND.get(case, case) in kinds(source, path)


@pytest.mark.parametrize("case", sorted(QUIET))
def test_exempt_code_is_not_reported(case: str) -> None:
    """Leave private, nested, caught and overloaded code alone.

    Args:
        case: name of the sample in QUIET.
    """
    assert kinds(QUIET[case]) == set()


def source_files(folder: str) -> Iterator[Path]:
    """List the Python files of a scope entry, skipping generated ones.

    Args:
        folder: folder, or single file, relative to the repository root.

    Yields:
        Each Python file in path order.
    """
    target = REPOSITORY_ROOT / folder
    for path in [target] if target.is_file() else sorted(target.rglob("*.py")):
        if not SKIPPED_PARTS & set(path.parts):
            yield path


@pytest.mark.parametrize("folder", SCOPE)
def test_scope_follows_rules_a_and_b(folder: str) -> None:
    """Keep every unit of the scope folder compliant.

    Args:
        folder: scope folder relative to the repository root.
    """
    found = [
        f"{path.relative_to(REPOSITORY_ROOT)}:{line} {kind} {detail}"
        for path in source_files(folder)
        for line, kind, detail in find_violations(
            path.read_text(encoding="utf-8"), str(path.relative_to(REPOSITORY_ROOT))
        )
    ]

    assert not found, "\n".join(found)
