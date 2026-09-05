"""Repository language convention tests."""

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPANISH_PROSE = re.compile(
    r"\b(?:"
    r"alcance|arquitectura|completad[oa]s?|configuración|consultá|decisiones|"
    r"desarrollo|devuelve|documentación|estado|evidencia|hecho|paquete|"
    r"parámetro|pendiente|plantillas|problemas|proyecto|próxim[oa]|retorna|"
    r"riesgo|tarea|verificad[oa]s?"
    r")\b",
    re.IGNORECASE,
)


def test_public_markdown_uses_english_prose() -> None:
    """Public repository documentation contains no known Spanish prose."""
    public_markdown = (ROOT / "README.md", *(ROOT / "docs").glob("*.md"))

    for path in public_markdown:
        match = SPANISH_PROSE.search(path.read_text())
        assert match is None, (path, match.group() if match else None)


def test_python_comments_and_docstrings_use_english_prose() -> None:
    """Tracked Python commentary contains no known Spanish prose."""
    for directory in (ROOT / "src", ROOT / "tools", ROOT / "tests"):
        for path in directory.rglob("*.py"):
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                docstring = ast.get_docstring(node, clean=False)
                match = SPANISH_PROSE.search(docstring or "")
                assert match is None, (path, getattr(node, "lineno", 1), match)

            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type != tokenize.COMMENT:
                    continue
                match = SPANISH_PROSE.search(token.string)
                assert match is None, (path, token.start[0], match)
