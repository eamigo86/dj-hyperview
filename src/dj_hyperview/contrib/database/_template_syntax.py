"""Linear-time lexing and compilation of Django template source for validation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import chain

from django.template import Engine, Origin
from django.template.base import DebugLexer, NodeList, Parser, Token

from dj_hyperview.validation import _forward_find

_OPENER = re.compile(r"\{[%{#]")
_CLOSER = {"{%": "%}", "{{": "}}", "{#": "#}"}


def _tag_spans(source: str) -> Iterator[tuple[int, int]]:
    """Yield the span of every Django tag, exactly as Django's tag_re finds it.

    A tag is an opener and the nearest closer of its kind on the same line. Django
    matches it with a lazy pattern, which reads on to the end of the line for every
    opener that has no closer, and so takes quadratic time on a long line of them.
    Here the nearest closer and the nearest line break are each searched once for
    all the openers that share them.
    """
    find = _forward_find(source)
    position = 0
    while (opener := _OPENER.search(source, position)) is not None:
        start = opener.start()
        closer = find(_CLOSER[opener.group()], start + 2)
        newline = find("\n", start + 2)
        if closer >= 0 and (newline < 0 or newline > closer):
            yield start, closer + 2
            position = closer + 2
        else:
            position = start + 1


class _LinearLexer(DebugLexer):
    """Django's debug lexer, producing the same tokens in linear time."""

    def tokenize(self) -> list[Token]:
        """Split the template into tokens that carry their source positions."""
        source = self.template_string
        tokens: list[Token] = []
        lineno = 1
        last = 0
        # As in Django, text and tags alternate and only empty strings are skipped.
        for start, end in chain(_tag_spans(source), [(len(source), len(source))]):
            for begin, finish, in_tag in ((last, start, False), (start, end, True)):
                if finish > begin:
                    text = source[begin:finish]
                    tokens.append(
                        self.create_token(text, (begin, finish), lineno, in_tag)
                    )
                    lineno += text.count("\n")
            last = end
        return tokens


def _compile_nodelist(engine: Engine, content: str, origin: Origin) -> NodeList:
    """Compile source with the engine's libraries, as Template does, in linear time."""
    parser = Parser(
        _LinearLexer(content).tokenize(),
        engine.template_libraries,
        engine.template_builtins,
        origin,
    )
    return parser.parse()
