"""Linear-time lexing of Django template source."""

import math
import re
from collections.abc import Callable, Iterator
from itertools import chain

from django.template.base import DebugLexer, Token

_OPENER = re.compile(r"\{[%{#]")
_CLOSER = {"{%": "%}", "{{": "}}", "{#": "#}"}


def _forward_find(document: str) -> Callable[[str, int], int]:
    """Return a str.find that never searches the same text twice for one marker.

    The searches of each marker must start at or after the previous one. A previous
    hit remains valid until it is consumed, and a previous miss covers every later
    suffix, so a scan that retries a marker from every opener stays linear.
    """
    positions: dict[str, int] = {}

    def find(marker: str, start: int) -> int:
        position = positions.get(marker)
        if position is None or 0 <= position < start:
            position = positions[marker] = document.find(marker, start)
        return position

    return find


def _scan(source: str) -> Iterator[tuple[int, int, int]]:
    """Yield what Django's tag_re makes of each opener, in one linear pass.

    A tag is an opener and the nearest closer of its kind on the same line. Django
    matches it with a lazy pattern, which reads on to the end of the line for every
    opener that has no closer, and so takes quadratic time on a long line of them.
    Here the nearest closer and the nearest line break are each searched once for
    all the openers that share them.

    Yields:
        The start of the opener, the end of its tag or -1 when it has none, and the
        end of its line.
    """
    find = _forward_find(source)
    position = 0
    while (opener := _OPENER.search(source, position)) is not None:
        start = opener.start()
        closer = find(_CLOSER[opener.group()], start + 2)
        newline = find("\n", start + 2)
        line_end = len(source) if newline < 0 else newline
        if closer >= 0 and (newline < 0 or newline > closer):
            yield start, closer + 2, line_end
            position = closer + 2
        else:
            yield start, -1, line_end
            position = start + 1


def _tag_spans(source: str) -> Iterator[tuple[int, int]]:
    """Yield the span of every Django tag, exactly as Django's tag_re finds it."""
    for start, end, _ in _scan(source):
        if end >= 0:
            yield start, end


def _unclosed_cost(source: str, limit: float = math.inf) -> int:
    """Count the characters Django's lexer reads for openers that close no tag.

    Each such opener makes the lazy pattern read on to the end of its line, so the
    sum is the work that Django's lexer wastes on the source. It is quadratic in the
    length of a line of openers, which is why it is counted here, in linear time,
    and not measured.

    Args:
        source: Template source.
        limit: A count that makes the exact sum unnecessary once it is passed.

    Returns:
        The sum, or a count over the limit when the scan stopped at it.
    """
    cost = 0
    for start, end, line_end in _scan(source):
        if end < 0:
            cost += line_end - start
            if cost > limit:
                break
    return cost


def _unclosed_cost_exceeds(source: str, limit: int) -> bool:
    """Tell whether the unclosed cost of the source is over the limit.

    Every unclosed opener holds a brace and costs at most the longest line, so a
    source whose braces times its longest line are within the limit cannot exceed it
    and needs no scan. Both factors are counted at C speed, and the scan, which runs
    in Python, is left to the sources that the bound cannot clear.

    Args:
        source: Template source.
        limit: The largest cost that is accepted.

    Returns:
        Whether the unclosed cost is over the limit, as the exact scan decides it.
    """
    braces = source.count("{")
    if braces == 0 or braces * max(map(len, source.split("\n"))) <= limit:
        return False
    return _unclosed_cost(source, limit) > limit


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
