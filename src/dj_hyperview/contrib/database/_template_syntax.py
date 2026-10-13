"""Compilation of Django template source for validation, in linear time."""

from __future__ import annotations

from django.template import Engine, Origin
from django.template.base import NodeList, Parser

from dj_hyperview._template_lexer import _LinearLexer

# Rendering uses three Python frames for each nested block, and the recursion limit
# of 1000 is shared with the caller. 330 nested blocks fail in an empty stack, and
# 230 fail below the 300 frames of a request that has reached its view.
_MAX_NESTING = 100


def _deepest_nesting(nodelist: NodeList) -> int:
    """Count the levels of blocks inside blocks, without recursing as Django does."""
    deepest = 0
    pending = [(nodelist, 0)]
    while pending:
        nodes, depth = pending.pop()
        deepest = max(deepest, depth)
        for node in nodes:
            for attribute in node.child_nodelists:
                if children := getattr(node, attribute, None):
                    pending.append((children, depth + 1))
    return deepest


def _compile_nodelist(engine: Engine, content: str, origin: Origin) -> NodeList:
    """Compile source with the engine's libraries, as Template does, in linear time.

    Raises:
        RecursionError: If the source nests blocks too deeply to compile, or too
            deeply to render in a stack that already holds a request.
    """
    parser = Parser(
        _LinearLexer(content).tokenize(),
        engine.template_libraries,
        engine.template_builtins,
        origin,
    )
    nodelist = parser.parse()
    if _deepest_nesting(nodelist) > _MAX_NESTING:
        raise RecursionError("template nesting exceeds what can be rendered")
    return nodelist
