"""Text that XML 1.0 cannot represent: validator, filters and final validation."""

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.utils.safestring import SafeString
from lxml import etree

from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.templatetags.dj_hyperview import hv_attr, hv_text
from dj_hyperview.validation import validate_hxml
from dj_hyperview.validators import validate_xml_text

from .stubs import TemplateSource

NAMESPACE = "xmlns='https://hyperview.org/hyperview'"
ILLEGAL_MESSAGE = "document contains characters not allowed in XML 1.0"
ILLEGAL = [
    "\x00",
    "\x01",
    "\x08",
    "\x0b",
    "\x0c",
    "\x0e",
    "\x1f",
    "\ud800",
    "\udbff",
    "\udc00",
    "\udfff",
    "\ufffe",
    "\uffff",
]
LEGAL = [
    "",
    "plain text",
    "tab\tnewline\ncarriage\rreturn",
    "emoji \U0001f600 and \U0010ffff",
    "latin caf\u00e9 and controls \x7f\x80\x9f",
    "\ud7ff\ue000\ufffd",
]


def render(source: str, **context: str) -> str:
    """Render one in-memory template through the validated Hyperview engine."""
    resolver = TemplateResolver([TemplateSource(content=source)])
    return HyperviewEngine(resolver).render("screen.xml", context)


@pytest.mark.parametrize("value", LEGAL)
def test_validator_accepts_text_that_xml_can_represent(value: str) -> None:
    """Tabs, line breaks, non-BMP characters and C1 controls are legal XML."""
    validate_xml_text(value)
    validate_hxml(f"<text {NAMESPACE}>{value}</text>")


@pytest.mark.parametrize("character", ILLEGAL)
def test_validator_rejects_each_illegal_character_class(character: str) -> None:
    """Controls, lone surrogates and the two noncharacters raise a stable code."""
    with pytest.raises(ValidationError) as captured:
        validate_xml_text(f"Buy{character}milk")

    assert captured.value.code == "xml_characters"
    assert "Buy" not in str(captured.value)


def test_validator_composes_with_django_form_fields() -> None:
    """The validator is an ordinary Django validator for forms and models."""
    field = forms.CharField(validators=[validate_xml_text])

    assert field.clean("Buy milk") == "Buy milk"
    with pytest.raises(forms.ValidationError) as captured:
        field.clean("Buy\x01milk")
    assert [error.code for error in captured.value.error_list] == ["xml_characters"]


def test_validator_character_class_matches_libxml2() -> None:
    """Every Basic Multilingual Plane character is classified as libxml2 does."""
    for code in range(0x10000):
        if 0xD800 <= code <= 0xDFFF or chr(code) in "<&":
            continue
        character = chr(code)
        try:
            etree.fromstring(f"<a>{character}</a>".encode())
        except etree.XMLSyntaxError:
            parsed = False
        else:
            parsed = True
        try:
            validate_xml_text(character)
        except ValidationError:
            accepted = False
        else:
            accepted = True
        assert accepted is parsed, hex(code)


def test_hv_text_removes_illegal_characters_then_escapes_markup() -> None:
    """The text filter keeps escaping and returns safe markup."""
    result = hv_text('Buy\x01 <milk> & "eggs"\ud800\uffff')

    assert result == "Buy &lt;milk&gt; &amp; &quot;eggs&quot;"
    assert isinstance(result, SafeString)


def test_hv_attr_encodes_attribute_whitespace_after_escaping() -> None:
    """Whitespace controls become references, and escaped ampersands stay single."""
    result = hv_attr('a\x01\nb\rc\td & "e"')

    assert result == "a&#10;b&#13;c&#9;d &amp; &quot;e&quot;"
    assert isinstance(result, SafeString)


def test_filters_are_not_double_escaped_by_template_autoescape() -> None:
    """The returned safe string is escaped once in an autoescaped template."""
    rendered = render(
        f"{{% load dj_hyperview %}}<text {NAMESPACE}>{{{{ value|hv_text }}}}</text>",
        value="Buy\x01milk & <eggs>",
    )

    assert rendered == f"<text {NAMESPACE}>Buymilk &amp; &lt;eggs&gt;</text>"


def test_hv_attr_round_trips_attribute_whitespace_through_a_real_parse() -> None:
    """Attribute newlines survive parsing only when encoded as references."""
    value = "line one\nline two\r\n\tindented\x01"
    wrapper = f'<view {NAMESPACE}><text value="{{{{ value|FILTER }}}}"/></view>'

    encoded = render(
        "{% load dj_hyperview %}" + wrapper.replace("FILTER", "hv_attr"), value=value
    )
    plain = render(
        "{% load dj_hyperview %}" + wrapper.replace("|FILTER", ""), value="a\nb"
    )

    assert (
        etree.fromstring(encoded.encode())[0].get("value")
        == "line one\nline two\r\n\tindented"
    )
    assert etree.fromstring(plain.encode())[0].get("value") == "a b"


@pytest.mark.parametrize("character", ILLEGAL)
def test_final_validation_names_illegal_characters_without_echoing_content(
    character: str,
) -> None:
    """A rejected document explains the cause but never repeats its content."""
    document = f"<text {NAMESPACE}>Buy{character}milk</text>"

    with pytest.raises(TemplateValidationError) as captured:
        validate_hxml(document)

    error = captured.value
    assert error.code == "malformed_xml"
    assert error.message == ILLEGAL_MESSAGE
    assert (error.line, error.column) == (None, None)
    assert "Buy" not in str(error) and character not in str(error)


def test_rendered_context_value_with_a_control_character_is_diagnosable() -> None:
    """The consumer outage scenario now reports an actionable fixed message."""
    with pytest.raises(TemplateValidationError) as captured:
        render(f"<text {NAMESPACE}>{{{{ title }}}}</text>", title="Buy\x01milk")

    assert str(captured.value) == (
        f"HXML validation failed [malformed_xml]: {ILLEGAL_MESSAGE}"
    )


def test_response_content_with_a_control_character_reports_the_same_cause() -> None:
    """Explicit response bodies use the same final-validation diagnostics."""
    with pytest.raises(TemplateValidationError) as captured:
        HyperviewResponse(f"<text {NAMESPACE}>Buy\x01milk</text>")

    assert captured.value.message == ILLEGAL_MESSAGE
