"""Linear lexing and XML-context scanning of stored template source match Django."""

import random
import re
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.template import Engine, Origin, Template, TemplateSyntaxError
from django.template.base import DebugLexer, Lexer, TokenType, tag_re

from dj_hyperview._template_lexer import _LinearLexer, _tag_spans, _unclosed_cost
from dj_hyperview.contrib.database import admin_validation as scanning
from dj_hyperview.contrib.database._template_syntax import _compile_nodelist

SEEDS = range(6000)
LEXER_ATOMS = (
    "{{",
    "}}",
    "{%",
    "%}",
    "{#",
    "#}",
    "{",
    "}",
    "%",
    "#",
    "\n",
    "\r",
    " ",
    "x",
    "{{ a }}",
    "{% if a %}",
    "{# note #}",
    "{% verbatim %}",
    "{% endverbatim %}",
    "{% verbatim n %}",
    "{% endverbatim n %}",
    "{%verbatim%}",
    "{%endverbatim%}",
    "{% comment %}",
    "{% endcomment %}",
)
TEMPLATE_ATOMS = (
    *LEXER_ATOMS,
    "{{ v|safe }}",
    "{{ v | safe }}",
    "{{ v|upper }}",
    "{{ v|safeseq }}",
    "{% load i18n %}",
    "{% trans 'a' %}",
    "{% comment 'note' %}",
    "<a>",
    "</a>",
    '<b c="1">',
    "<b c='1'>",
    "<d/>",
    "<!--",
    "-->",
    "<![CDATA[",
    "]]>",
    "<?pi ",
    '<?xml version="1.0"?>',
    "<?XML ?>",
    "?>",
    "]]",
    '"',
    "'",
    "<",
    ">",
    "=",
    "text",
)


# Mostly XML, so that tags, quoted values, comments and CDATA are all reached.
XML_ATOMS = (
    "<a ",
    "<b",
    "c=",
    '"',
    "'",
    ">",
    "/>",
    "<!--",
    "-->",
    "<![CDATA[",
    "]]>",
    "<?pi ",
    "<?XML ",
    "?>",
    "]]",
    " ",
    "x",
    "\n",
    "{{ v }}",
    "{% if v %}",
    "{% endif %}",
    "{% trans 'a' %}",
    "{# c #}",
    "{{ v|safe }}",
    "{{ v|safeseq }}",
)


def _random_source(seed: int, atoms: tuple[str, ...]) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(atoms) for _ in range(rng.randint(0, 24)))


def _signature(tokens: list[Any]) -> list[tuple[Any, ...]]:
    return [(t.token_type, t.contents, t.position, t.lineno) for t in tokens]


def test_tag_spans_match_djangos_tag_regular_expression() -> None:
    for seed in SEEDS:
        source = _random_source(seed, LEXER_ATOMS)

        assert list(_tag_spans(source)) == [
            m.span() for m in tag_re.finditer(source)
        ], source


def test_linear_lexer_produces_the_tokens_of_djangos_debug_lexer() -> None:
    for seed in SEEDS:
        source = _random_source(seed, LEXER_ATOMS)

        assert _signature(_LinearLexer(source).tokenize()) == _signature(
            DebugLexer(source).tokenize()
        ), source


@pytest.mark.parametrize(
    "source",
    [
        "{{\n}}",
        "{# note\n#}",
        "{%\nif x\n%}",
        "{{{ a }}}",
        "{{#}}",
        "{{%  %}",
        "{%}%}",
        "{{ a }}}",
        "{#}#}",
        "{{ a }} {{ b",
        "{{ a\n}} {{ b }}",
    ],
)
def test_tag_spans_keep_djangos_delimiter_corner_cases(source: str) -> None:
    assert list(_tag_spans(source)) == [m.span() for m in tag_re.finditer(source)]


_OPENERS = {"{{", "{%", "{#"}


def _characters_read_by_djangos_lexer(source: str) -> int:
    """Count what tag_re reads for openers that start no tag, the slow way.

    Each such opener makes the lazy pattern read on to the end of its line.
    """
    inside_a_tag = {i for m in tag_re.finditer(source) for i in range(*m.span())}
    cost = 0
    for index in range(len(source) - 1):
        if index not in inside_a_tag and source[index : index + 2] in _OPENERS:
            line_end = source.find("\n", index)
            cost += (len(source) if line_end < 0 else line_end) - index
    return cost


def test_unclosed_cost_matches_what_djangos_lexer_reads() -> None:
    for seed in SEEDS:
        source = _random_source(seed, LEXER_ATOMS)

        assert _unclosed_cost(source) == _characters_read_by_djangos_lexer(source), (
            source
        )


@pytest.mark.parametrize(
    ("source", "cost"),
    [
        ("", 0),
        ("{{ a }}<x/>{% if b %}{# c #}", 0),
        ("{{ ", 3),
        ("{{ a }} {% b", 4),
        ("{# a\n{# b #}\n{% c", 4 + 4),
        ("{{ }}\n{{ a\n}}", 4),
        ("{" * 4_000, 4_000 * 4_001 // 2 - 1),
        ("{{ " * 40_000, 3 * 40_000 * 40_001 // 2),
        ("<view>" + "{{ a }}" * 100_000, 0),
    ],
)
def test_unclosed_cost_counts_the_line_that_each_unclosed_opener_makes_django_read(
    source: str, cost: int
) -> None:
    assert _unclosed_cost(source) == cost


def test_unclosed_cost_stops_counting_once_over_the_limit() -> None:
    # The first opener alone reads the whole line, so the scan ends there.
    assert _unclosed_cost("{" * 1_000_000, limit=10) == 1_000_000


@pytest.mark.parametrize(
    "build",
    [lambda: "{{ " * 40_000, lambda: "{{ a\n" * 40_000 + "}}", lambda: "{#" * 60_000],
    ids=["unclosed-openers", "distant-closer", "comment-openers"],
)
def test_unclosed_openers_are_lexed_in_linear_time(build: Callable[[], str]) -> None:
    """Django's lazy regular expression rescans the line for every opener."""
    source = build()

    started = time.perf_counter()
    tokens = _LinearLexer(source).tokenize()
    elapsed = time.perf_counter() - started

    assert [token.token_type for token in tokens] == [TokenType.TEXT]
    assert elapsed < 3  # about 10 s for Django's own lexer on the first input


def test_compiled_nodelist_matches_the_one_djangos_template_builds() -> None:
    engine = Engine(debug=True)
    source = (
        "{% if a %}{{ b|upper }}{% else %}{% comment 'x' %}y{% endcomment %}{% endif %}"
    )

    nodelist = _compile_nodelist(engine, source, Origin("hyperview:x"))

    assert [type(node).__name__ for node in nodelist] == [
        type(node).__name__ for node in Template(source, engine=engine).nodelist
    ]


def test_compiled_nodelist_reports_syntax_errors_with_the_source_line() -> None:
    with pytest.raises(TemplateSyntaxError) as captured:
        _compile_nodelist(Engine(), "<a>\n{% if x %}", Origin("hyperview:x"))

    assert captured.value.token.lineno == 2


# The reference below is deliberately slow and literal: it lexes with Django's own
# tag regular expression and walks the XML text one character at a time.


def _blank(span: str, marker: str = "") -> str:
    return marker + "".join("\n" if character == "\n" else " " for character in span)


def _reference_advance(text: str, state: str) -> str:
    closing = {"dq": '"', "sq": "'", "comment": "-->", "cdata": "]]>", "pi": "?>"}
    index = 0
    while index < len(text):
        if state == "text":
            if text.startswith("<!--", index):
                state, index = "comment", index + 4
            elif text.startswith("<![CDATA[", index):
                state, index = "cdata", index + 9
            elif text.startswith("<?", index):
                state, index = "pi", index + 2
            else:
                state = "tag" if text[index] == "<" else "text"
                index += 1
        elif state == "tag":
            character = text[index]
            index += 1
            if character == ">":
                state = "text"
            elif character == '"':
                state = "dq"
            elif character == "'":
                state = "sq"
        elif text.startswith(closing[state], index):
            index += len(closing[state])
            state = "tag" if state in {"dq", "sq"} else "text"
        else:
            index += 1
    return state


def _reference_scan(source: str) -> dict[str, Any]:
    value_marker, structure_marker = "DJHVSTATICDYNAMIC", "DJHVSTATICSTRUCTURE"
    while value_marker in source:
        value_marker += "_"
    while structure_marker in source:
        structure_marker += "_"
    lexer = Lexer(source)
    tokens, position, lineno = [], 0, 1
    for index, piece in enumerate(tag_re.split(source)):
        if piece:
            token = lexer.create_token(piece, None, lineno, index % 2 == 1)
            tokens.append((token, position, position + len(piece)))
            lineno += piece.count("\n")
        position += len(piece)

    parts: list[str] = []
    contexts: dict[int, str] = {}
    first_structure_line = verbatim_line = None
    state = "text"
    index = 0
    while index < len(tokens):
        token, start, end = tokens[index]
        index += 1
        if token.token_type is TokenType.TEXT:
            state = _reference_advance(source[start:end], state)
            parts.append(source[start:end])
            continue
        if token.token_type is TokenType.COMMENT:
            parts.append(_blank(source[start:end]))
            continue
        command = ""
        if token.token_type is TokenType.BLOCK:
            command = token.contents.split(maxsplit=1)[0] if token.contents else ""
            closers = [
                later
                for later in range(index, len(tokens))
                if tokens[later][0].token_type is TokenType.BLOCK
                and tokens[later][0].contents == "endcomment"
            ]
            if command == "comment" and closers:
                parts.append(_blank(source[start : tokens[closers[0]][2]]))
                index = closers[0] + 1
                continue
            if token.contents[:9] in {"verbatim", "verbatim "}:
                verbatim_line = verbatim_line or token.lineno
                later = index
                while later < len(tokens) and (
                    tokens[later][0].token_type is TokenType.TEXT
                ):
                    later += 1
                stop = tokens[later][2] if later < len(tokens) else len(source)
                parts.append(_blank(source[start:stop]))
                index = later + 1
                continue
            if command == "load":
                parts.append(_blank(source[start:end]))
                continue
        context = {"tag": "tag", "dq": "attribute", "sq": "attribute"}.get(
            state, "text"
        )
        contexts[start] = context
        if token.token_type is TokenType.VAR:
            structure = context == "tag" or (
                context == "text" and re.search(r"\|\s*safe\b", token.contents)
            )
        else:
            structure = context != "attribute" and command not in scanning._SCALAR_TAGS
        if structure and first_structure_line is None:
            first_structure_line = token.lineno
        parts.append(
            _blank(source[start:end], structure_marker if structure else value_marker)
        )
    masked = re.sub(
        r"<\?xml(?:\s|\?)[\s\S]*?\?>",
        lambda match: _blank(match.group()),
        "".join(parts),
        flags=re.IGNORECASE,
    )
    return {
        "masked": masked,
        "value_marker": value_marker,
        "structure_marker": structure_marker,
        "contexts": contexts,
        "first_structure_line": first_structure_line,
        "verbatim_line": verbatim_line,
    }


def test_scan_matches_a_character_by_character_reference() -> None:
    for seed in SEEDS:
        source = _random_source(seed, (TEMPLATE_ATOMS, XML_ATOMS)[seed % 2])

        scan = scanning._scan_template(source)

        assert {
            "masked": scan.masked,
            "value_marker": scan.value_marker,
            "structure_marker": scan.structure_marker,
            "contexts": scan.contexts,
            "first_structure_line": scan.first_structure_line,
            "verbatim_line": scan.verbatim_line,
        } == _reference_scan(source), source


def test_scan_keeps_every_line_break() -> None:
    for seed in SEEDS:
        source = _random_source(seed, (TEMPLATE_ATOMS, XML_ATOMS)[seed % 2])

        assert scanning._scan_template(source).masked.count("\n") == source.count(
            "\n"
        ), source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('<a b="{{ x }}">{{ y }}</a>', ["attribute", "text"]),
        ("<a {{ x }}/>", ["tag"]),
        ("<a b='>{{ x }}'/>", ["attribute"]),
        ("<!-- {{ x }} -->", ["text"]),
        ("<![CDATA[ {% if x %} ]]>", ["text"]),
        ("<?pi {{ x }}?>{{ y }}", ["text", "text"]),
        ("Don't {{ x }}", ["text"]),
        ("<a b='1' c=\"{{ x }}\"/>", ["attribute"]),
        ("{# no\n#}{{ x }}", ["text"]),
        ("<a {% if x %}b='1'{% endif %}/>", ["tag", "tag"]),
        ("<a b='{% if x %}1{% endif %}'/>", ["attribute", "attribute"]),
        ("<?><a b='{{ x }}'/>", ["text"]),
        ("<!--><a b='{{ x }}'/>", ["text"]),
        ("<!----><a b='{{ x }}'/>", ["attribute"]),
        ("<![CDATA[]]><a b='{{ x }}'/>", ["attribute"]),
        ("<a b='1'{{ x }}>", ["tag"]),
    ],
)
def test_scan_locates_each_django_token_in_xml_text_a_tag_or_an_attribute(
    source: str, expected: list[str]
) -> None:
    assert list(scanning._scan_template(source).contexts.values()) == expected


def test_scan_hides_comment_blocks_whatever_their_note() -> None:
    source = '<a>{% comment "why" %}<b x=1>{% if %}{% endcomment %}</a>'

    scan = scanning._scan_template(source)

    assert scan.masked == "<a>" + " " * (len(source) - len("<a></a>")) + "</a>"
    assert scan.first_structure_line is None


def test_scan_does_not_take_a_tag_that_starts_with_comment_for_a_comment() -> None:
    source = "<a>{% commentx %}<b x=1>{% endcomment %}</a>"

    scan = scanning._scan_template(source)

    assert "<b x=1>" in scan.masked
    assert scan.first_structure_line == 1


def test_scan_treats_an_unclosed_comment_as_one_runtime_token() -> None:
    scan = scanning._scan_template("{% comment %}\n<b x=1>")

    assert scan.first_structure_line == 1
    assert scan.masked.endswith("\n<b x=1>")


def test_scan_reports_the_first_verbatim_line_and_hides_its_body() -> None:
    body = "{% verbatim %}<b {{ x }}>{% endverbatim %}"

    scan = scanning._scan_template(f"<a/>\n{body}<c/>")

    assert scan.verbatim_line == 2
    assert scan.masked == "<a/>\n" + " " * len(body) + "<c/>"


def test_scan_stops_at_an_unclosed_verbatim_block() -> None:
    body = "{% verbatim %}<b/>"

    scan = scanning._scan_template(f"<a/>{body}")

    assert scan.masked == "<a/>" + " " * len(body)
    assert scan.verbatim_line == 1


def test_markers_stay_unique_whatever_the_underscore_runs_in_the_source() -> None:
    source = " ".join(f"DJHVSTATICDYNAMIC{'_' * run}" for run in (0, 3, 1))

    scan = scanning._scan_template(source)

    assert scan.value_marker == "DJHVSTATICDYNAMIC" + "_" * 4
    assert scan.value_marker not in source
    assert scan.structure_marker == "DJHVSTATICSTRUCTURE"


def test_declarations_are_blanked_only_when_they_close() -> None:
    closed = scanning._scan_template('<?xml version="1.0"?><a/>').masked
    unclosed = scanning._scan_template('<?xml version="1.0"<a/>').masked

    assert closed == " " * len('<?xml version="1.0"?>') + "<a/>"
    assert unclosed == '<?xml version="1.0"<a/>'


@pytest.mark.parametrize(
    "build",
    [
        lambda: "<?xml " * 40_000,
        lambda: "{{ a }}" + "<!--" * 40_000,
        lambda: "<a b='" * 40_000,
        lambda: "<![CDATA[" * 40_000,
        lambda: "<?xml ?>" * 40_000,
    ],
    ids=["declarations", "comments", "quotes", "cdata", "closed-declarations"],
)
def test_scanning_unterminated_xml_constructs_takes_linear_time(
    build: Callable[[], str],
) -> None:
    source = build()

    started = time.perf_counter()
    scanning._scan_template(source)

    assert time.perf_counter() - started < 3  # 30 to 50 s with unanchored regexes


def test_stored_source_validation_reports_excessive_nesting() -> None:
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source("{% if x %}" * 3000 + "{% endif %}" * 3000)

    assert captured.value.code == "too_deep"
    assert captured.value.messages == ["Template is nested too deeply."]


NESTED_BLOCKS = {
    "if": ("{% if a %}", "{% endif %}"),
    "for": ("{% for x in a %}", "{% endfor %}"),
    "with": ("{% with b=a %}", "{% endwith %}"),
    "spaceless": ("{% spaceless %}", "{% endspaceless %}"),
    "autoescape": ("{% autoescape on %}", "{% endautoescape %}"),
    # Django checks a filter's arguments with inspect, which turns a RecursionError
    # into a TypeError.
    "if-filter": ("{% if a|upper %}", "{% endif %}"),
    "filter": ("{% filter lower %}", "{% endfilter %}"),
    "with-filter": ("{% with b=a|upper %}", "{% endwith %}"),
    "for-filter": ("{% for x in a|slice:':1' %}", "{% endfor %}"),
}


def nested(tag: str, depth: int) -> str:
    opening, closing = NESTED_BLOCKS[tag]
    return opening * depth + "<view />" + closing * depth


@pytest.mark.parametrize("tag", NESTED_BLOCKS)
def test_nesting_at_the_limit_is_accepted_and_renders_below_a_deep_caller(
    tag: str,
) -> None:
    """Compiling takes about 490 nested blocks, but rendering fails at about 330."""
    from dj_hyperview.contrib.database._template_syntax import _MAX_NESTING
    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )
    from dj_hyperview.engine import HyperviewEngine

    source = nested(tag, _MAX_NESTING)
    validate_stored_template_source(source)
    template = HyperviewEngine().backend.from_string(source)

    def below_a_request(frames: int) -> str:
        if frames:
            return below_a_request(frames - 1)
        return template.render({"a": [1]})

    assert below_a_request(300) == "<view />"


@pytest.mark.parametrize("tag", NESTED_BLOCKS)
def test_nesting_over_the_limit_is_too_deep_before_rendering_can_fail(
    tag: str,
) -> None:
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database._template_syntax import _MAX_NESTING
    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source(nested(tag, _MAX_NESTING + 1))

    assert captured.value.code == "too_deep"


@pytest.mark.parametrize("tag", NESTED_BLOCKS)
def test_nesting_far_over_the_limit_is_too_deep_instead_of_a_crash(tag: str) -> None:
    """A filter in an opening tag raised TypeError at about 495 levels, not too_deep."""
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source(nested(tag, 600))

    assert captured.value.code == "too_deep"


@pytest.mark.parametrize("body", ["<view />", ""], ids=["with-content", "empty"])
def test_nesting_is_capped_at_100_levels_whatever_the_innermost_body_holds(
    body: str,
) -> None:
    """The limit is pinned here, so a change to _MAX_NESTING has to change this."""
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    def source(depth: int) -> str:
        return "{% if a %}" * depth + body + "{% endif %}" * depth

    validate_stored_template_source(source(100))
    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source(source(101))
    assert captured.value.code == "too_deep"


def test_a_recursion_error_that_django_turns_into_a_type_error_is_still_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inspect.getfullargspec raises TypeError from the RecursionError it caught."""

    def exhausted(self: object, token: str) -> None:
        try:
            raise RecursionError
        except RecursionError as error:
            raise TypeError("unsupported callable") from error

    monkeypatch.setattr("django.template.base.Parser.compile_filter", exhausted)

    with pytest.raises(RecursionError):
        _compile_nodelist(Engine(), "{{ a|upper }}", Origin("hyperview:x"))


def test_an_unrelated_type_error_is_not_mistaken_for_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: object, token: str) -> None:
        raise TypeError("not about recursion")

    monkeypatch.setattr("django.template.base.Parser.compile_filter", broken)

    with pytest.raises(TypeError, match="not about recursion"):
        _compile_nodelist(Engine(), "{{ a|upper }}", Origin("hyperview:x"))


def test_an_else_branch_does_not_deepen_the_nesting() -> None:
    from dj_hyperview.contrib.database._template_syntax import _MAX_NESTING
    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    source = "{% if a %}x{% elif b %}y{% else %}z{% endif %}" * (_MAX_NESTING * 3)

    validate_stored_template_source(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{% if x %}", "Invalid Django template syntax at line 1."),
        ("<a>\n{% unknown_tag %}", "Invalid Django template syntax at line 2."),
        ("{{ x|no_such_filter }}", "Invalid Django template syntax at line 1."),
    ],
)
def test_stored_source_validation_keeps_reporting_django_syntax_errors(
    source: str, message: str
) -> None:
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source(source)

    assert (captured.value.code, captured.value.messages) == (
        "django_syntax",
        [message],
    )


def test_stored_source_validation_rejects_unclosed_openers_in_linear_time() -> None:
    """Serving compiles with Django's lexer, which took 10 s on the first source."""
    from django.core.exceptions import ValidationError

    from dj_hyperview.contrib.database.validators import (
        validate_stored_template_source,
    )

    validate_stored_template_source("{{ " * 1_000)
    started = time.perf_counter()
    with pytest.raises(ValidationError) as captured:
        validate_stored_template_source("<view>" + "{{ " * 40_000)

    assert captured.value.code == "max_unclosed"
    assert captured.value.messages == [
        "Invalid Hyperview template source (max_unclosed)."
    ]
    assert time.perf_counter() - started < 5
