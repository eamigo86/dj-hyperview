"""Compilation of Django template source for validation, in linear time."""

from __future__ import annotations

from django.template import Engine, Origin
from django.template.base import NodeList, Parser

from dj_hyperview._template_lexer import _LinearLexer


def _compile_nodelist(engine: Engine, content: str, origin: Origin) -> NodeList:
    """Compile source with the engine's libraries, as Template does, in linear time."""
    parser = Parser(
        _LinearLexer(content).tokenize(),
        engine.template_libraries,
        engine.template_builtins,
        origin,
    )
    return parser.parse()
