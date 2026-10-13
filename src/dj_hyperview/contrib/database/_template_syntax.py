"""Compilation of Django template source for validation, in linear time."""

from __future__ import annotations

from django.template import Engine, Origin
from django.template.base import NodeList, Parser

from dj_hyperview._template_lexer import _LinearLexer

# Rendering uses three Python frames for each nested block, and the recursion limit
# of 1000 is shared with the caller. 330 nested blocks fail in an empty stack, and
# 230 fail below 300 frames. A view was measured 8 to 24 frames deep in a plain Django
# request, so 300 is a generous allowance for a server, middleware and the caller.
_MAX_NESTING = 100


class _NestingParser(Parser):
    """Django's parser, stopped at the nesting depth that can still be rendered.

    Counting while parsing ends the recursion early. Django's parser recurses once per
    nested block, and a filter in an opening tag turns the RecursionError into a
    TypeError when it checks the filter's arguments, if the stack ends there.
    """

    _depth = 0

    def parse(self, parse_until: list[str] | None = None) -> NodeList:
        """Parse a block's body, or the whole source, one level deeper."""
        if self._depth > _MAX_NESTING:
            raise RecursionError("template nesting exceeds what can be rendered")
        self._depth += 1
        try:
            return super().parse(parse_until)
        finally:
            self._depth -= 1


def _caused_by_recursion(error: BaseException | None) -> bool:
    """Tell whether a RecursionError is anywhere in the chain of an exception."""
    while error is not None:
        if isinstance(error, RecursionError):
            return True
        error = error.__cause__ or error.__context__
    return False


def _compile_nodelist(engine: Engine, content: str, origin: Origin) -> NodeList:
    """Compile source with the engine's libraries, as Template does, in linear time.

    Raises:
        RecursionError: If the source nests blocks too deeply to compile, or too
            deeply to render in a stack that already holds a request.
    """
    parser = _NestingParser(
        _LinearLexer(content).tokenize(),
        engine.template_libraries,
        engine.template_builtins,
        origin,
    )
    try:
        return parser.parse()
    except Exception as error:
        if _caused_by_recursion(error) and not isinstance(error, RecursionError):
            raise RecursionError(
                "template nesting exceeds what can be compiled"
            ) from error
        raise
