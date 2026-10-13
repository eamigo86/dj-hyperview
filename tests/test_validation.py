import random
from types import SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings

import dj_hyperview.validation as validation_module
from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import (
    IGNORED_BLOCKS,
    _contains_forbidden_declaration,
    _django_comment_end,
    _inline_django_comment_end,
    _rendered_hxml_text,
    validate_fragment_hxml,
    validate_hxml,
    validate_rendered_hxml,
    validate_template_source,
)

from .stubs import TemplateSource


def assert_validation_error(code, action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == code
    return error.value


def test_validate_hxml_accepts_a_well_formed_document():
    document = "<view xmlns='https://hyperview.org/hyperview'><text>Ready</text></view>"

    assert validate_hxml(document) == document


def test_validate_hxml_rejects_malformed_xml_with_a_stable_error():
    error = assert_validation_error("malformed_xml", lambda: validate_hxml("<view>"))

    assert str(error) == "HXML validation failed [malformed_xml]: invalid XML"


class TrackedDocument(str):
    """A document that counts how often the scanner asks where a token starts."""

    startswith_calls = 0

    def startswith(self, *args, **kwargs):
        self.startswith_calls += 1
        return super().startswith(*args, **kwargs)


def test_declaration_scan_has_a_constant_time_common_path() -> None:
    """Documents without declaration literals avoid the block-aware scan."""
    document = TrackedDocument(f"<view>{'content' * 10_000}</view>")

    assert _contains_forbidden_declaration(document) is False
    assert document.startswith_calls == 0


@pytest.mark.parametrize("template_source", [False, True])
def test_declaration_scan_visits_only_candidate_positions(
    template_source: bool,
) -> None:
    """A literal inside a comment sends the scan down the block-aware path.

    Only a few positions can start anything the scan acts on. It used to test every
    character there: about 1 MB took 215 ms rendered and 373 ms as template source.
    """
    document = TrackedDocument(
        "<!-- <!DOCTYPE -->" + "<text>content {{ value }}</text>" * 20_000
    )

    assert (
        _contains_forbidden_declaration(document, template_source=template_source)
        is False
    )
    assert document.startswith_calls < 50


def reference_contains_forbidden_declaration(
    document: str, *, template_source: bool = False
) -> bool:
    """The scanner as it was before it jumped between candidates, one index at a time.

    The oracle of the differential test below. The Django comment helpers are
    shared with the implementation, so this pins only the scanning loop.
    """
    if "<!DOCTYPE" not in document and "<!ENTITY" not in document:
        return False
    positions: dict[str, int] = {}

    def find(marker: str, start: int) -> int:
        position = positions.get(marker)
        if position is None or 0 <= position < start:
            position = positions[marker] = document.find(marker, start)
        return position

    index = 0
    while index < len(document):
        for opening, closing in IGNORED_BLOCKS:
            if document.startswith(opening, index):
                end = find(closing, index + len(opening))
                index = len(document) if end < 0 else end + len(closing)
                break
        else:
            comment_end = None
            if template_source:
                comment_end = _inline_django_comment_end(document, index, find)
                if comment_end is None:
                    comment_end = _django_comment_end(document, index, find)
            if comment_end is not None:
                index = comment_end
            elif document.startswith(("<!DOCTYPE", "<!ENTITY"), index):
                return True
            else:
                index += 1
    return False


SCAN_TOKENS = [
    "<!--",
    "-->",
    "<![CDATA[",
    "]]>",
    "<?",
    "?>",
    "<!DOCTYPE",
    "<!ENTITY",
    "<!",
    "<",
    "{",
    "#",
    "%",
    "{#",
    "#}",
    "{%",
    "%}",
    "{% comment %}",
    "{%comment%}",
    '{% comment "note" %}',
    "{% endcomment %}",
    "{% endcomment%}",
    "\n",
    "\r",
    " ",
    "text",
    "<view>",
    "</view>",
]


def random_scan_document(rng: random.Random) -> str:
    """Join delimiters, declarations and plain runs in an order no author would."""
    parts = []
    for _ in range(rng.randint(1, 16)):
        if rng.random() < 0.2:
            parts.append("x" * rng.randint(1, 40))
        else:
            parts.append(rng.choice(SCAN_TOKENS))
    return "".join(parts)


def test_declaration_scan_matches_the_character_by_character_scanner() -> None:
    """Jumping between candidates is equivalent to testing every index."""
    rng = random.Random(20261013)
    with_literal = 0
    outcomes: set[tuple[bool, bool]] = set()
    for _ in range(4_000):
        document = random_scan_document(rng)
        with_literal += "<!DOCTYPE" in document or "<!ENTITY" in document
        for template_source in (False, True):
            expected = reference_contains_forbidden_declaration(
                document, template_source=template_source
            )
            actual = _contains_forbidden_declaration(
                document, template_source=template_source
            )
            assert actual is expected, (template_source, document)
            outcomes.add((template_source, expected))

    # The sample must reach the block-aware path and both of its answers.
    assert with_literal > 1_000
    assert outcomes == {(False, False), (False, True), (True, False), (True, True)}


@pytest.mark.parametrize("max_depth", [256])
def test_libxml_depth_ceiling_uses_the_public_max_depth_code(max_depth) -> None:
    """Parser-level depth rejection is reported as the configured limit."""
    document = "<view>" * 257 + "</view>" * 257

    assert_validation_error(
        "max_depth",
        lambda: validate_hxml(document, config=ValidationSettings(max_depth=max_depth)),
    )


RESOURCE_LIMIT = validation_module.etree.ErrorTypes.ERR_RESOURCE_LIMIT
INTERNAL_ERROR = validation_module.etree.ErrorTypes.ERR_INTERNAL_ERROR
EXCESSIVE_DEPTH = "Excessive depth in document: 256 use XML_PARSE_HUGE option"


@pytest.mark.parametrize(
    ("message", "libxml_code", "public_code"),
    [
        # Libxml message-prefix changes do not alter the stable package code.
        ("Maximum DEPTH limit reached", RESOURCE_LIMIT, "max_depth"),
        # A resource limit that is not about depth is not MAX_DEPTH.
        ("xmlSAX2Characters: huge text node", RESOURCE_LIMIT, "malformed_xml"),
        # Another code does not classify a parser error as a depth failure...
        ("Maximum DEPTH limit reached", 0, "malformed_xml"),
        (
            "Opening and ending tag mismatch: depth line 1 and deep",
            INTERNAL_ERROR,
            "malformed_xml",
        ),
        # ...except libxml2's own message: before 2.13 it came as an internal error.
        (EXCESSIVE_DEPTH, INTERNAL_ERROR, "max_depth"),
        (EXCESSIVE_DEPTH, RESOURCE_LIMIT, "max_depth"),
    ],
)
def test_parser_errors_map_to_max_depth_only_for_libxml2_depth_limits(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    libxml_code: int,
    public_code: str,
) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise validation_module.etree.XMLSyntaxError(message, libxml_code, 0, 0)

    monkeypatch.setattr(validation_module.etree, "fromstring", reject)

    assert_validation_error(public_code, lambda: validate_hxml("<view />"))


@pytest.mark.parametrize(
    "document",
    [
        "<depth></deep>",
        "<view depth='1' depth='2' />",
        "<view><depth></view></depth>",
    ],
)
def test_syntax_errors_that_merely_mention_depth_are_malformed_xml(document) -> None:
    """libxml2 echoes names into messages, so a tag or attribute can say "depth"."""
    error = assert_validation_error("malformed_xml", lambda: validate_hxml(document))

    assert error.message == "invalid XML"


@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
def test_non_utf8_xml_declarations_are_rejected(validator) -> None:
    """The XML declaration cannot contradict the UTF-8 HTTP contract."""
    document = '<?xml version="1.0" encoding="ISO-8859-1"?><view>café</view>'

    error = assert_validation_error("invalid_encoding", lambda: validator(document))

    assert error.__cause__ is None


@pytest.mark.parametrize("encoding", ["UTF-8\x00", "\x00", "UTF-8\U0000d800"])
@pytest.mark.parametrize(
    "validator",
    [
        validate_template_source,
        validate_hxml,
        validate_fragment_hxml,
        validate_rendered_hxml,
    ],
)
def test_encoding_names_that_cannot_be_looked_up_are_invalid_encoding(
    validator, encoding: str
) -> None:
    """A NUL or a lone surrogate is no name character; lookup raises ValueError."""
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

    error = assert_validation_error("invalid_encoding", lambda: validator(document))

    assert error.message == "XML declaration must use UTF-8"
    assert error.__cause__ is None


@pytest.mark.parametrize(
    "encoding",
    [
        "a" * 65,
        "UTF-8" + "x" * 200_000,
        "1utf-8",
        "utf 8",
        "utf-8;",
        "utf-8\xe9",
        "-utf-8",
    ],
    ids=[
        "too-long",
        "huge",
        "digit-first",
        "space",
        "punctuation",
        "non-ascii",
        "dash",
    ],
)
@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
def test_malformed_or_oversized_encoding_names_never_reach_the_codec_registry(
    monkeypatch: pytest.MonkeyPatch, validator, encoding: str
) -> None:
    """Python keeps every name it looks up for the life of the process.

    A name that XML does not allow, or that is longer than any codec name, is
    rejected by its shape, so hostile declarations cannot grow that cache.
    """

    def forbidden(name: str) -> None:
        raise AssertionError("codecs.lookup must not see an invalid name")

    monkeypatch.setattr(
        validation_module, "codecs", SimpleNamespace(lookup=forbidden), raising=True
    )
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

    error = assert_validation_error("invalid_encoding", lambda: validator(document))

    assert error.message == "XML declaration must use UTF-8"
    assert error.__cause__ is None


@pytest.mark.parametrize("encoding", ["UTF-8", "utf-8", "utf8", "UTF_8", "U8"])
def test_every_spelling_of_utf8_is_still_accepted(encoding: str) -> None:
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

    assert validate_hxml(document) == document


def test_render_validation_strips_template_whitespace_before_xml_declaration() -> None:
    """Django load tags may precede a declaration without breaking the response."""
    document = (
        '\n\t<?xml version="1.0" encoding="UTF-8"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

    assert validate_rendered_hxml(document).startswith("<?xml")


def test_render_validation_strips_whitespace_after_an_initial_bom() -> None:
    """The documented BOM contract also holds for the public normalizer."""
    document = (
        '\ufeff\n\t<?xml version="1.0" encoding="UTF-8"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

    assert validate_rendered_hxml(document) == document.replace("\n\t", "")


ILLEGAL_MESSAGE = "document contains characters not allowed in XML 1.0"
# Python treats all of these as whitespace; XML 1.0 allows only space, tab, CR, LF.
ILLEGAL_SPACES = ["\x0b", "\x0c", "\x1c", "\x1f"]
LEGAL_NON_SPACES = ["\x85", "\xa0", "\U00002028", "\U00003000"]
NOT_XML_WHITESPACE = [*ILLEGAL_SPACES, *LEGAL_NON_SPACES]


@pytest.mark.parametrize("bom", ["", "\U0000feff"])
@pytest.mark.parametrize("whitespace", [" ", "\t", "\r", "\n", " \t\r\n\n "])
def test_render_normalizer_strips_xml_whitespace_after_an_optional_bom(
    bom: str, whitespace: str
) -> None:
    """Only the four XML whitespace characters may precede the document."""
    assert _rendered_hxml_text(f"{bom}{whitespace}<view />") == f"{bom}<view />"


@pytest.mark.parametrize("character", NOT_XML_WHITESPACE)
def test_render_normalizer_keeps_characters_that_are_not_xml_whitespace(
    character: str,
) -> None:
    """str.lstrip would drop these silently, including illegal control characters."""
    document = f"{character}<view />"

    assert _rendered_hxml_text(document) == document


@pytest.mark.parametrize(
    ("character", "message"),
    [
        *[(character, ILLEGAL_MESSAGE) for character in ILLEGAL_SPACES],
        *[(character, "invalid XML") for character in LEGAL_NON_SPACES],
    ],
)
def test_leading_unicode_whitespace_is_rejected_not_stripped(
    character: str, message: str
) -> None:
    """The public normalizer and the engine report what precedes the root."""
    document = f"{character}<view xmlns='https://hyperview.org/hyperview' />"
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=document)]))

    direct = assert_validation_error(
        "malformed_xml", lambda: validate_rendered_hxml(document)
    )
    rendered = assert_validation_error(
        "malformed_xml", lambda: engine.render("screen.xml")
    )

    assert direct.message == rendered.message == message


@pytest.mark.parametrize(
    ("document", "config", "code"),
    [
        ("<!DOCTYPE view>\x01<view />", ValidationSettings(), "forbidden_declaration"),
        (
            '<?xml version="1.0" encoding="ISO-8859-1"?>\x01<view />',
            ValidationSettings(),
            "invalid_encoding",
        ),
        ("<view>\x01\xe9</view>", ValidationSettings(max_bytes=14), "max_bytes"),
    ],
)
def test_guard_codes_take_precedence_over_illegal_characters(document, config, code):
    """A control character must not mask a failure that protects the parser."""
    assert_validation_error(code, lambda: validate_hxml(document, config=config))


@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
@pytest.mark.parametrize(
    ("document", "config"),
    [
        ("<!DOCTYPE view>\U0000d800<view />", ValidationSettings()),
        ("<view>\U0000d800\xe9</view>", ValidationSettings(max_bytes=14)),
    ],
)
def test_a_lone_surrogate_is_reported_before_size_and_declarations(
    validator, document, config
):
    """The guard encodes first, and a lone surrogate cannot be encoded."""
    error = assert_validation_error(
        "malformed_xml", lambda: validator(document, config=config)
    )

    assert error.message == ILLEGAL_MESSAGE


def test_a_non_utf8_declaration_is_reported_before_a_lone_surrogate():
    """The declaration is read from the text, before anything is encoded."""
    document = '<?xml version="1.0" encoding="ISO-8859-1"?>\U0000d800<view />'

    assert_validation_error("invalid_encoding", lambda: validate_hxml(document))


@pytest.mark.parametrize(
    "document",
    [
        "<!DOCTYPE view><view />",
        '<!DOCTYPE view [<!ENTITY xxe SYSTEM "http://127.0.0.1/secret">]>'
        "<view>&xxe;</view>",
        '<!DOCTYPE view [<!ENTITY a "1234567890"><!ENTITY b "&a;&a;">]>'
        "<view>&b;</view>",
    ],
)
def test_validate_hxml_denies_dtd_external_and_expanding_entities(document):
    error = assert_validation_error(
        "forbidden_declaration", lambda: validate_hxml(document)
    )

    assert error.message == "DTD and entity declarations are forbidden"


@pytest.mark.parametrize(
    ("document", "config", "code"),
    [
        ("<view>é</view>", ValidationSettings(max_bytes=14), "max_bytes"),
        (
            "<view><body><text /></body></view>",
            ValidationSettings(max_depth=2),
            "max_depth",
        ),
        ("<view><text /><text /></view>", ValidationSettings(max_nodes=2), "max_nodes"),
    ],
)
def test_validate_hxml_enforces_configured_limits(document, config, code):
    assert_validation_error(code, lambda: validate_hxml(document, config=config))


@pytest.mark.parametrize(
    ("body", "limit", "needed"),
    [
        ("<!-- note --><?audit data?>", "max_nodes", 3),
        ("<text><!-- note --></text>", "max_depth", 3),
        ("<text><?audit data?></text>", "max_depth", 3),
    ],
)
def test_comments_and_processing_instructions_count_toward_the_limits(
    body: str, limit: str, needed: int
) -> None:
    """MAX_NODES and MAX_DEPTH bound every node, not only elements: the safer bound."""
    document = f"<view xmlns='https://hyperview.org/hyperview'>{body}</view>"

    exact = ValidationSettings(**{limit: needed})
    assert validate_hxml(document, config=exact) == document
    one_less = ValidationSettings(**{limit: needed - 1})
    assert_validation_error(limit, lambda: validate_hxml(document, config=one_less))


def test_comments_and_processing_instructions_outside_the_root_are_not_counted() -> (
    None
):
    """The walk starts at the root element, so only MAX_BYTES bounds its siblings."""
    document = (
        "<!-- before --><?audit data?>"
        "<view xmlns='https://hyperview.org/hyperview' />"
        "<!-- after -->"
    )

    assert validate_hxml(document, config=ValidationSettings(max_nodes=1)) == document


def test_engine_safe_render_escapes_context_and_validates_output():
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        "<text xmlns='https://hyperview.org/hyperview'>"
                        "{{ value }}</text>"
                    )
                )
            ]
        )
    )

    assert engine.render_hxml("screen.xml", {"value": "<&"}) == (
        "<text xmlns='https://hyperview.org/hyperview'>&lt;&amp;</text>"
    )


@pytest.mark.parametrize(
    ("content", "config", "context", "code"),
    [
        (
            "<!DOCTYPE view><view>{% unknown %}</view>",
            ValidationSettings(),
            None,
            "forbidden_declaration",
        ),
        (
            "<view>{{ value }}</view>",
            ValidationSettings(max_bytes=30),
            {"value": "x" * 40},
            "max_bytes",
        ),
        ("<view />", ValidationSettings(max_bytes=1), None, "max_bytes"),
    ],
)
def test_engine_rejects_unsafe_source_or_rendered_output(
    content, config, context, code
):
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content=content)]), validation=config
    )

    assert_validation_error(code, lambda: engine.render_hxml("screen.xml", context))


def test_engine_cannot_request_retired_publish_only_mode():
    with pytest.raises(TypeError):
        ValidationSettings(mode="publish")


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": "<view>{{ value|safe }}</view>"},
            }
        ]
    }
)
def test_public_render_and_response_reject_malformed_post_render_output():
    direct_error = assert_validation_error(
        "malformed_xml",
        lambda: render_template("screen.xml", {"value": "<broken>"}),
    )

    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"),
        "screen.xml",
        {"value": "<broken>"},
    )
    response_error = assert_validation_error("malformed_xml", response.render)

    assert direct_error.message == "invalid XML"
    assert response_error.message == "invalid XML"


def test_validation_contract_is_exported_from_package_root():
    import dj_hyperview

    assert dj_hyperview.TemplateValidationError is TemplateValidationError
    assert dj_hyperview.validate_template_source is validate_template_source
    assert dj_hyperview.validate_hxml is validate_hxml
