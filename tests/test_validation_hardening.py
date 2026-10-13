import pytest
from django.test import RequestFactory, override_settings

from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import validate_hxml, validate_template_source

from .stubs import TemplateSource

MALFORMED_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {"screen.xml": "<view xmlns='https://hyperview.org/hyperview'>"},
                )
            ]
        },
    }
]


def assert_malformed(action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == "malformed_xml"
    assert error.value.message == "invalid XML"


def test_public_engine_render_rejects_malformed_hxml():
    engine = HyperviewEngine(
        TemplateResolver(
            [TemplateSource(content="<view xmlns='https://hyperview.org/hyperview'>")]
        )
    )

    assert_malformed(lambda: engine.render("screen.xml"))


@pytest.mark.parametrize("using", ["django"])
@override_settings(TEMPLATES=MALFORMED_TEMPLATES, HYPERVIEW={})
def test_template_response_validates_django_fallbacks(using):
    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"), "screen.xml", using=using
    )

    assert response.is_rendered is False
    assert_malformed(response.render)
    assert response.is_rendered is False


def test_isolated_unicode_surrogate_is_a_typed_failure():
    with pytest.raises(TemplateValidationError) as error:
        validate_hxml("\ud800")

    assert error.value.code == "malformed_xml"
    assert error.value.message == (
        "document contains characters not allowed in XML 1.0"
    )


@pytest.mark.parametrize(
    "document",
    [
        "<text xmlns='https://hyperview.org/hyperview'>"
        "<!-- <!DOCTYPE view> --><text /></text>",
        "<text xmlns='https://hyperview.org/hyperview'><![CDATA[<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]]></text>",
    ],
)
def test_declaration_text_is_allowed_in_xml_comments_and_cdata(document):
    assert validate_hxml(document) == document


@pytest.mark.parametrize(
    "comment",
    [
        "{# <!DOCTYPE view> #}",
        "{% comment %}<!ENTITY xxe SYSTEM 'file:///etc/passwd'>{% endcomment %}",
        '{% comment "reason" %}<!DOCTYPE view>{% endcomment %}',
    ],
)
def test_declaration_text_is_allowed_in_django_comments(comment):
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=f"<view xmlns='https://hyperview.org/hyperview'>{comment}<text>ok</text></view>"
                )
            ]
        )
    )

    assert (
        engine.render("screen.xml")
        == "<view xmlns='https://hyperview.org/hyperview'><text>ok</text></view>"
    )


def test_multiline_django_comment_syntax_cannot_hide_a_declaration() -> None:
    """The source guard mirrors Django's single-line comment lexer."""
    source = "{#\n<!DOCTYPE view [<!ENTITY x 'unsafe'>]>\n#}<view xmlns='https://hyperview.org/hyperview'>&x;</view>"

    with pytest.raises(TemplateValidationError) as captured:
        validate_template_source(source)

    assert captured.value.code == "forbidden_declaration"


def test_bare_carriage_return_remains_inside_an_inline_django_comment() -> None:
    """Declaration scanning mirrors Django's LF-only inline-comment boundary."""
    source = (
        "{# note\r<!DOCTYPE view> #}<view xmlns='https://hyperview.org/hyperview' />"
    )
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=source)]))

    assert (
        engine.render("screen.xml")
        == "<view xmlns='https://hyperview.org/hyperview' />"
    )


def test_csrf_tag_can_precede_an_xml_declaration_on_its_own_line() -> None:
    """Post-render normalization removes whitespace emitted by a load tag."""
    source = (
        "{% load dj_hyperview %}\n"
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<view xmlns='https://hyperview.org/hyperview'>{% hv_csrf_token %}</view>"
    )
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=source)]))

    rendered = engine.render("screen.xml", request=RequestFactory().get("/form/"))

    assert rendered.startswith("<?xml")
    assert 'name="csrfmiddlewaretoken"' in rendered


@pytest.mark.parametrize(
    "source",
    [
        "{% %}",
        "{% unfinished",
        "{% comment %}",
        "{% comment %}<!DOCTYPE view>",
        "{% comment %}{% unfinished <!ENTITY xxe>",
        "{% comment %}{% include 'ignored' %}<!DOCTYPE view>{% endcomment %}",
    ],
)
def test_source_guard_defers_malformed_django_comments_to_compilation(source):
    assert validate_template_source(source) == source


def test_active_and_post_render_constructed_declarations_remain_forbidden():
    with pytest.raises(TemplateValidationError) as source_error:
        validate_template_source(
            "<!DOCTYPE view><view xmlns='https://hyperview.org/hyperview' />"
        )

    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        "{{ payload|safe }}"
                        "<view xmlns='https://hyperview.org/hyperview' />"
                    )
                )
            ]
        )
    )
    payload = '<!DOCTYPE view [<!ENTITY xxe SYSTEM "http://127.0.0.1/secret">]>'
    with pytest.raises(TemplateValidationError) as rendered_error:
        engine.render("screen.xml", {"payload": payload})

    assert source_error.value.code == "forbidden_declaration"
    assert rendered_error.value.code == "forbidden_declaration"


HYPERVIEW_XMLNS = "xmlns='https://hyperview.org/hyperview'"


def prefixed_namespaces(count: int, *, start: int = 0) -> str:
    return " ".join(f"xmlns:p{i}='urn:p{i}'" for i in range(start, start + count))


def assert_element_limit(document: str, fragment: str) -> None:
    with pytest.raises(TemplateValidationError) as error:
        validate_hxml(document)
    assert error.value.code == "max_attributes"
    assert fragment in error.value.message
    assert "p0" not in str(error.value)
    assert "a0" not in str(error.value)


def test_attribute_flood_is_rejected_before_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-element cap bounds super-linear XSD work without timing assertions."""

    def forbidden_schema_walk(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema validation must not run")

    monkeypatch.setattr(
        "dj_hyperview.schema._validate_schema_root", forbidden_schema_walk
    )
    attributes = " ".join(f"a{i}='1'" for i in range(300))

    assert_element_limit(f"<view {HYPERVIEW_XMLNS} {attributes} />", "256 attributes")


def test_attribute_cap_counts_one_element_and_allows_its_exact_limit() -> None:
    """Many elements may carry attributes; only a single element is capped."""
    exact = " ".join(f"a{i}='1'" for i in range(256))
    spread = "".join(f"<text id='t{i}' style='s{i}'>{i}</text>" for i in range(300))

    with pytest.raises(TemplateValidationError) as exact_error:
        validate_hxml(f"<view {HYPERVIEW_XMLNS} {exact} />")
    validate_hxml(f"<view {HYPERVIEW_XMLNS}>{spread}</view>")

    assert exact_error.value.code == "schema"


def test_namespace_flood_is_rejected_on_the_declaring_element() -> None:
    """The default namespace counts, so 65 prefixes exceed 64 in-scope bindings."""
    assert_element_limit(
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(65)} />",
        "64 in-scope namespaces",
    )


def test_inherited_namespaces_count_toward_the_in_scope_cap() -> None:
    """No single element declares more than 64, but a descendant inherits them."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(40)}>"
        f"<text {prefixed_namespaces(30, start=40)}>ok</text></view>"
    )

    assert_element_limit(document, "64 in-scope namespaces")


def test_namespace_cap_allows_its_exact_limit() -> None:
    """The default binding plus 63 prefixes is exactly 64 in-scope namespaces."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(63)}><text>ok</text></view>"
    )

    assert validate_hxml(document) == document


def test_comments_and_processing_instructions_pass_the_element_caps() -> None:
    """Non-element nodes have no attributes or namespaces to count."""
    document = (
        f"<view {HYPERVIEW_XMLNS}><!-- note --><?audit data?><text>ok</text></view>"
    )

    assert validate_hxml(document) == document
