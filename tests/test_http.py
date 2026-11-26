"""HTTP response, template response and view integration tests."""

import encodings
import uuid
from collections.abc import Callable, Iterator

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.template import TemplateDoesNotExist
from django.template.response import ContentNotRenderedError, TemplateResponse
from django.test import RequestFactory, override_settings

import dj_hyperview
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import (
    HYPERVIEW_FRAGMENT_MEDIA_TYPE,
    HYPERVIEW_MEDIA_TYPE,
    HyperviewFragmentResponse,
    HyperviewFragmentTemplateResponse,
    HyperviewResponse,
    HyperviewTemplateResponse,
)
from dj_hyperview.views import HyperviewTemplateView

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "screen.xml": (
                            '<text xmlns="https://hyperview.org/hyperview">{{'
                            " title }}</text>"
                        )
                    },
                )
            ]
        },
    }
]
HYPERVIEW_SOURCES = {
    "SOURCES": [
        {
            "BACKEND": "tests.stubs.TemplateSource",
            "OPTIONS": {
                "content": (
                    '<text xmlns="https://hyperview.org/hyperview">{{ title }}</text>'
                )
            },
        }
    ]
}


@override_settings(DEFAULT_CHARSET="iso-8859-1")
def test_hyperview_response_preserves_http_contract() -> None:
    """A Hyperview response preserves status, headers and the UTF-8 contract."""
    response = HyperviewResponse(
        '<text xmlns="https://hyperview.org/hyperview">Accepted</text>',
        status=202,
        headers={"X-Screen": "accepted"},
    )

    assert (
        response.content
        == b'<text xmlns="https://hyperview.org/hyperview">Accepted</text>'
    )
    assert response.charset == "utf-8"
    assert response.status_code == 202
    assert response.headers["X-Screen"] == "accepted"
    assert response.headers["Content-Type"] == f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"


def test_hyperview_response_preserves_explicit_content_type_and_utf8() -> None:
    """An explicit content type is kept and the body is still UTF-8."""
    document = '<text xmlns="https://hyperview.org/hyperview">olá</text>'
    response = HyperviewResponse(
        document,
        content_type="application/xml",
        charset="utf-8",
        status=207,
        reason="Multi-Status",
    )
    assert response.content == document.encode("utf-8")
    assert response.charset == "utf-8"
    assert response.status_code == 207
    assert response.reason_phrase == "Multi-Status"
    assert response.headers["Content-Type"] == "application/xml"


def test_foreign_media_type_still_rejects_a_non_utf8_charset() -> None:
    """A foreign media type still rejects a non-UTF-8 charset."""
    with pytest.raises(ValueError, match="UTF-8"):
        HyperviewResponse("olá", content_type="text/plain; charset=iso-8859-1")


@pytest.mark.parametrize(
    "content_type",
    [
        f"{HYPERVIEW_MEDIA_TYPE}; charset=iso-8859-1",
        f'{HYPERVIEW_MEDIA_TYPE}; charset="iso-8859-1"',
    ],
)
def test_hyperview_response_rejects_non_utf8_content_type_charset(
    content_type: str,
) -> None:
    """The media-type parameter cannot silently override the UTF-8 contract.

    Args:
        content_type: Content-Type header value with a non-UTF-8 charset.
    """
    with pytest.raises(ValueError, match="Hyperview responses require UTF-8"):
        HyperviewResponse(
            '<text xmlns="https://hyperview.org/hyperview" />',
            content_type=content_type,
        )


def test_hyperview_response_parses_quoted_content_type_parameters() -> None:
    """Quoted response parameters survive standards-based charset parsing."""
    response = HyperviewResponse(
        '<text xmlns="https://hyperview.org/hyperview" />',
        content_type=f'{HYPERVIEW_MEDIA_TYPE}; profile="mobile app"; charset="UTF-8"',
    )

    assert response.headers["Content-Type"] == (
        f'{HYPERVIEW_MEDIA_TYPE}; profile="mobile app"; charset=utf-8'
    )


@pytest.mark.parametrize(
    "parameter",
    [
        "profile=mobile",
        'profile="mobile app"',
        'profile="a;b"',
        'profile="a,b"',
        r'profile="a\"b"',
        r'profile="a\\b"',
        'profile=""',
        'profile="a=b"',
        # A star inside a quoted value is data, not an extended parameter name.
        'profile="a; b*=c"',
        r'profile="a\"; b*=c"',
    ],
)
def test_content_type_parameters_are_requoted_per_rfc_9110(parameter: str) -> None:
    """A parameter value that is not a token is quoted and escaped, never raw.

    Args:
        parameter: Content-Type parameter appended to the media type.
    """
    from django.utils.http import parse_header_parameters

    response = HyperviewResponse(
        '<text xmlns="https://hyperview.org/hyperview" />',
        content_type=f"{HYPERVIEW_MEDIA_TYPE}; {parameter}",
    )

    header = response.headers["Content-Type"]
    assert header == f"{HYPERVIEW_MEDIA_TYPE}; {parameter}; charset=utf-8"
    assert (
        parse_header_parameters(header)[1]["profile"]
        == (parse_header_parameters(f"x/y; {parameter}")[1]["profile"])
    )


@pytest.mark.parametrize(
    "parameter",
    [
        "a b=c",
        "a(b)=c",
        'profile="a\x00b"',
        'profile="a\x7fb"',
        'profile="\u20ac"',
        'profile="\u0100"',
        # RFC 8187 extended names: Django drops the star and decodes the value, and
        # email reads the parameter as encoded, so neither gives the value back.
        "a*=x",
        "profile=mobile; a*=utf-8''x%20y",
        'a *="x"',
        "a* =x",
        "a*\t= x",
        "a**=x",
        # Django ends a parameter at a semicolon after an even count of quotes, a
        # quote behind a backslash not counted, so these hide the star from a reader
        # that follows RFC quoting but not from Django.
        r"\";a*=x",
        r"\";a**=",
        r"\"a;a*='a",
        r'""\";a*=',
        # An unknown encoding in an extended value: Django 5.2 raises LookupError.
        "a*=''%",
        "a*=bogus''%41",
        # Django and email read the quote after a trailing backslash as escaped.
        r'profile="a\\"',
        r'profile="a\\\\"',
        r'profile="\\"',
    ],
)
def test_content_type_parameters_that_cannot_be_represented_are_rejected(
    parameter: str,
) -> None:
    """Content-Type parameters that cannot be represented are rejected.

    Args:
        parameter: Content-Type parameter appended to the media type.
    """
    with pytest.raises(ValueError, match="Content-Type parameter"):
        HyperviewResponse(
            '<text xmlns="https://hyperview.org/hyperview" />',
            content_type=f"{HYPERVIEW_MEDIA_TYPE}; {parameter}",
        )


@pytest.mark.parametrize(
    "header",
    [
        f"{HYPERVIEW_MEDIA_TYPE}; a*",
        f"{HYPERVIEW_MEDIA_TYPE}; a*; profile=mobile",
    ],
)
def test_a_content_type_parameter_without_a_value_is_dropped_by_django(
    header: str,
) -> None:
    """Django keeps no name that has no "=", star or not, so nothing is altered.

    That is unlike "a*=x", which Django keeps as "a=x" and so must be rejected.

    Args:
        header: Content-Type header value with a parameter that has no value.
    """
    response = HyperviewResponse(VALID_HXML, content_type=header)

    assert response.headers["Content-Type"] in {
        f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8",
        f"{HYPERVIEW_MEDIA_TYPE}; profile=mobile; charset=utf-8",
    }


@pytest.mark.parametrize("media_type", [HYPERVIEW_MEDIA_TYPE, "text/plain"])
@pytest.mark.parametrize("unit", ["x", "a=b;", ";"])
def test_a_content_type_over_10000_characters_is_rejected_before_parsing(
    media_type: str, unit: str
) -> None:
    """Django 6.1 refuses such a header and 5.2 has no cap, so both are held to it.

    Reading the parameters is quadratic in their number, and 5.2 takes about 15 s
    over 1 MB of semicolons.

    Args:
        media_type: Media type that the response class is expected to use.
        unit: Text repeated to pad the header over the limit.
    """
    prefix, suffix = f"{media_type}; p=", "; charset=utf-8"

    def header(length: int) -> str:
        return prefix + (unit * length)[: length - len(prefix) - len(suffix)] + suffix

    assert len(header(10_000)) == 10_000
    assert HyperviewResponse(VALID_HXML, content_type=header(10_000)).status_code == 200
    for length in (10_001, 12_000):
        with pytest.raises(ValueError, match="Invalid Content-Type parameter"):
            HyperviewResponse(VALID_HXML, content_type=header(length))


@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_is_the_default_media_type(
    response_type: type[HyperviewResponse], media_type: str
) -> None:
    """Passing None gives the media type of the class, as the default does.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
    """
    document = '<text xmlns="https://hyperview.org/hyperview" />'

    assert response_type(document, content_type=None).headers["Content-Type"] == (
        f"{media_type}; charset=utf-8"
    )


@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewTemplateResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentTemplateResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_is_the_default_media_type_for_template_responses(
    response_type: type[HyperviewTemplateResponse], media_type: str
) -> None:
    """None as content type gives the media type of the template response class.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
    """
    response = response_type(None, "screen.xml", content_type=None)

    assert response.headers["Content-Type"] == f"{media_type}; charset=utf-8"


@pytest.mark.parametrize("header", ["Content-Type", "content-type", "CONTENT-TYPE"])
@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_keeps_a_content_type_header(
    response_type: type[HyperviewResponse], media_type: str, header: str
) -> None:
    """None means not given, so a Content-Type header is not a second source.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
        header: Case variant of the Content-Type header name.
    """
    document = '<text xmlns="https://hyperview.org/hyperview" />'

    response = response_type(
        document, content_type=None, headers={header: f"{media_type}; charset=utf-8"}
    )

    assert response.headers["Content-Type"] == f"{media_type}; charset=utf-8"


@pytest.mark.parametrize("header", ["Content-Type", "content-type"])
@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewTemplateResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentTemplateResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_keeps_a_content_type_header_for_template_responses(
    response_type: type[HyperviewTemplateResponse], media_type: str, header: str
) -> None:
    """None as content type keeps a Content-Type header on template responses.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
        header: Case variant of the Content-Type header name.
    """
    response = response_type(
        None,
        "screen.xml",
        content_type=None,
        headers={header: f"{media_type}; charset=utf-8"},
    )

    assert response.headers["Content-Type"] == f"{media_type}; charset=utf-8"


def _one_shot_headers(content_type: str) -> Iterator[tuple[str, str]]:
    """Headers as a generator, which can be read only once."""
    return (
        pair
        for pair in [
            ("Cache-Control", "no-store"),
            ("Content-Type", f"{content_type}; charset=utf-8"),
        ]
    )


@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_keeps_every_header_of_a_one_shot_iterable(
    response_type: type[HyperviewResponse], media_type: str
) -> None:
    """None as content type keeps every header of a one-shot iterable.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
    """
    document = '<text xmlns="https://hyperview.org/hyperview" />'

    response = response_type(
        document, content_type=None, headers=_one_shot_headers(media_type)
    )

    assert dict(response.headers) == {
        "Cache-Control": "no-store",
        "Content-Type": f"{media_type}; charset=utf-8",
    }


@pytest.mark.parametrize(
    "response_type,media_type",
    [
        (HyperviewTemplateResponse, HYPERVIEW_MEDIA_TYPE),
        (HyperviewFragmentTemplateResponse, HYPERVIEW_FRAGMENT_MEDIA_TYPE),
    ],
)
def test_none_content_type_keeps_every_header_of_a_one_shot_iterable_for_templates(
    response_type: type[HyperviewTemplateResponse], media_type: str
) -> None:
    """None as content type keeps every one-shot header of template responses.

    Args:
        response_type: Response class under test.
        media_type: Media type that the response class is expected to use.
    """
    response = response_type(
        None, "screen.xml", content_type=None, headers=_one_shot_headers(media_type)
    )

    assert dict(response.headers) == {
        "Cache-Control": "no-store",
        "Content-Type": f"{media_type}; charset=utf-8",
    }


def test_a_content_type_header_still_rejects_a_non_utf8_charset() -> None:
    """A Content-Type header still rejects a non-UTF-8 charset."""
    with pytest.raises(ValueError, match="Hyperview responses require UTF-8"):
        HyperviewResponse(
            '<text xmlns="https://hyperview.org/hyperview" />',
            content_type=None,
            headers={"Content-Type": f"{HYPERVIEW_MEDIA_TYPE}; charset=latin-1"},
        )


def test_fragment_response_uses_fragment_media_type_and_utf8() -> None:
    """Direct fragments expose the Hyperview replacement media contract."""
    response = HyperviewFragmentResponse(
        '<text xmlns="https://hyperview.org/hyperview"><text>Café</text></text>'
    )

    assert (
        response.content
        == (
            '<text xmlns="https://hyperview.org/hyperview"><text>Café</text></text>'
        ).encode()
    )
    assert response.headers["Content-Type"] == (
        f"{HYPERVIEW_FRAGMENT_MEDIA_TYPE}; charset=utf-8"
    )


@pytest.mark.parametrize("root", ["doc", "navigator", "screen", "body"])
def test_fragment_response_rejects_document_roots(root: str) -> None:
    """Replacement fragments cannot contain client-owned document roots.

    Args:
        root: Client-owned document root that the fragment must not use.
    """
    with pytest.raises(TemplateValidationError) as error:
        HyperviewFragmentResponse(f"<{root} />")

    assert error.value.code == "restricted_fragment_root"


@override_settings(HYPERVIEW=HYPERVIEW_SOURCES)
def test_fragment_template_response_is_lazy_and_validates_its_root() -> None:
    """Template fragments remain lazy and enforce the replacement shape."""
    response = HyperviewFragmentTemplateResponse(
        RequestFactory().get("/fragment"), "fragment.xml", {"title": "A & B"}
    )

    assert response.is_rendered is False
    assert (
        response.render().content
        == b'<text xmlns="https://hyperview.org/hyperview">A &amp; B</text>'
    )
    assert response.headers["Content-Type"] == (
        f"{HYPERVIEW_FRAGMENT_MEDIA_TYPE}; charset=utf-8"
    )


@override_settings(
    TEMPLATES=TEMPLATES,
    HYPERVIEW=HYPERVIEW_SOURCES,
    DEFAULT_CHARSET="iso-8859-1",
)
def test_template_response_preserves_lazy_render_contract() -> None:
    """A template response keeps the lazy render contract."""
    request = RequestFactory().get("/screen")
    response = HyperviewTemplateResponse(
        request,
        "screen.xml",
        {"title": "A & B"},
        status=201,
        headers={"X-Screen": "created"},
    )

    assert response.is_rendered is False
    with pytest.raises(ContentNotRenderedError):
        _ = response.content

    response.render()

    assert response.is_rendered is True
    assert (
        response.content
        == b'<text xmlns="https://hyperview.org/hyperview">A &amp; B</text>'
    )
    assert response.charset == "utf-8"
    assert response.status_code == 201
    assert response.headers["X-Screen"] == "created"
    assert response.headers["Content-Type"] == f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"


@override_settings(TEMPLATES=TEMPLATES)
def test_template_response_defers_missing_template_error_until_render() -> None:
    """A template response defers the missing template error until render."""
    response = HyperviewTemplateResponse(
        RequestFactory().get("/missing"),
        "missing.xml",
    )

    assert response.is_rendered is False
    with pytest.raises(TemplateDoesNotExist, match="missing.xml"):
        response.render()
    assert response.is_rendered is False


@override_settings(TEMPLATES=TEMPLATES, HYPERVIEW={})
def test_template_response_requires_explicit_django_engine_without_sources() -> None:
    """Implicit response rendering never changes engines with configuration."""
    package_response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"),
        "screen.xml",
        {"title": "package"},
    )
    django_response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"),
        "screen.xml",
        {"title": "django"},
        using="django",
    )

    with pytest.raises(TemplateDoesNotExist):
        package_response.render()
    assert (
        django_response.render().content
        == b'<text xmlns="https://hyperview.org/hyperview">django</text>'
    )


@override_settings(TEMPLATES=TEMPLATES, HYPERVIEW=HYPERVIEW_SOURCES)
def test_template_view_renders_consumer_template_and_context() -> None:
    """The template view renders the consumer template with its context."""
    view = HyperviewTemplateView.as_view(
        template_name="screen.xml",
        extra_context={"title": "Consumer screen"},
    )

    response = view(RequestFactory().get("/screen"))

    assert isinstance(response, HyperviewTemplateResponse)
    assert response.is_rendered is False
    assert response.context_data["title"] == "Consumer screen"
    assert response.context_data["view"].template_name == "screen.xml"

    response.render()

    assert (
        response.content
        == b'<text xmlns="https://hyperview.org/hyperview">Consumer screen</text>'
    )
    assert response.headers["Content-Type"] == f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"


@override_settings(TEMPLATES=TEMPLATES, HYPERVIEW=HYPERVIEW_SOURCES)
def test_template_view_forwards_response_status_and_headers() -> None:
    """The template view forwards the response status and headers."""

    class AcceptedView(HyperviewTemplateView):
        template_name = "screen.xml"

        def render_to_response(
            self, context: dict[str, object], **response_kwargs: object
        ) -> TemplateResponse:
            return super().render_to_response(
                context,
                status=202,
                headers={"X-Screen": "accepted"},
                **response_kwargs,
            )

    response = AcceptedView.as_view()(RequestFactory().get("/accepted"))

    assert response.status_code == 202
    assert response.headers["X-Screen"] == "accepted"
    assert response.is_rendered is False
    assert (
        response.render().content
        == b'<text xmlns="https://hyperview.org/hyperview"></text>'
    )


def test_template_view_requires_a_consumer_template_name() -> None:
    """The template view requires a consumer template name."""
    view = HyperviewTemplateView.as_view()

    with pytest.raises(ImproperlyConfigured, match="requires either a definition"):
        view(RequestFactory().get("/missing-template"))


def test_http_api_is_available_from_the_package_namespace() -> None:
    """The HTTP API is available from the package namespace."""
    assert dj_hyperview.HYPERVIEW_MEDIA_TYPE == "application/vnd.hyperview+xml"
    assert dj_hyperview.HYPERVIEW_FRAGMENT_MEDIA_TYPE == (
        "application/vnd.hyperview_fragment+xml"
    )
    assert dj_hyperview.HyperviewFragmentResponse is HyperviewFragmentResponse
    assert (
        dj_hyperview.HyperviewFragmentTemplateResponse
        is HyperviewFragmentTemplateResponse
    )
    assert dj_hyperview.HyperviewResponse is HyperviewResponse
    assert dj_hyperview.HyperviewTemplateResponse is HyperviewTemplateResponse
    assert dj_hyperview.HyperviewTemplateView is HyperviewTemplateView


# Automatic final-body validation is independent of response media-type overrides.
VALID_HXML = '<view xmlns="https://hyperview.org/hyperview"><text>Ready</text></view>'


@pytest.mark.parametrize(
    "content",
    [
        b"",
        "<view>",
        '<view xmlns="https://hyperview.org/hyperview" typo="x"/>',
        b"\xff",
    ],
)
def test_eager_response_rejects_invalid_final_content(content: bytes | str) -> None:
    """An eager response rejects invalid final content.

    Args:
        content: Final content that is not a valid Hyperview document.
    """
    with pytest.raises(TemplateValidationError):
        HyperviewResponse(content)


def test_foreign_media_type_cannot_disable_hyperview_validation() -> None:
    """A foreign media type cannot disable Hyperview validation."""
    with pytest.raises(TemplateValidationError):
        HyperviewResponse("not XML", content_type="text/plain")
    with pytest.raises(ValueError, match="UTF-8"):
        HyperviewResponse(VALID_HXML, content_type="application/xml", charset="latin1")


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("mutation", ["content", "write", "writelines"])
def test_invalid_response_mutations_are_atomic(
    response_type: type[HyperviewResponse], mutation: str
) -> None:
    """Invalid response mutations are atomic.

    Args:
        response_type: Response class under test.
        mutation: Way the invalid content is assigned, as content, write or writelines.
    """
    response = response_type(VALID_HXML)
    before = response.content
    with pytest.raises(TemplateValidationError):
        if mutation == "content":
            response.content = "<invalid>"
        elif mutation == "write":
            response.write("<invalid>")
        else:
            response.writelines(["<invalid", ">"])
    assert response.content == before


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("mutation", ["construct", "content", "write", "writelines"])
def test_lone_surrogates_get_the_actionable_xml_character_message(
    response_type: type[HyperviewResponse], mutation: str
) -> None:
    """Text that cannot be encoded fails like every other XML 1.0 violation.

    Args:
        response_type: Response class under test.
        mutation: Way the surrogate reaches the response, as construct, content,
            write or writelines.
    """
    surrogate = "\U0000d800"
    response = None if mutation == "construct" else response_type(VALID_HXML)
    with pytest.raises(TemplateValidationError) as error:
        if mutation == "construct":
            response_type(f"<text>{surrogate}</text>")
        elif mutation == "content":
            response.content = surrogate
        elif mutation == "write":
            response.write(surrogate)
        else:
            response.writelines(["<text>", surrogate])

    assert error.value.code == "malformed_xml"
    assert error.value.message == "document contains characters not allowed in XML 1.0"
    if response is not None:
        assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize("mutation", ["construct", "content"])
def test_a_nul_in_the_declared_encoding_is_a_typed_failure(
    response_type: type[HyperviewResponse], as_bytes: bool, mutation: str
) -> None:
    """codecs.lookup raises a bare ValueError for NUL, which must not escape.

    Args:
        response_type: Response class under test.
        as_bytes: Whether the document is passed as bytes instead of text.
        mutation: Way the document reaches the response, as construct or content.
    """
    document = (
        '<?xml version="1.0" encoding="UTF-8\x00"?>'
        '<text xmlns="https://hyperview.org/hyperview" />'
    )
    value = document.encode() if as_bytes else document
    response = None if mutation == "construct" else response_type(VALID_HXML)
    with pytest.raises(TemplateValidationError) as error:
        if response is None:
            response_type(value)
        else:
            response.content = value

    assert error.value.code == "invalid_encoding"
    assert error.value.message == "XML declaration must use UTF-8"
    if response is not None:
        assert response.content == VALID_HXML.encode()


def test_writelines_assembles_once_before_validating() -> None:
    """Writelines assembles all chunks once before it validates."""
    response = HyperviewResponse(VALID_HXML)
    response.writelines(iter(["<!--", "comment", "-->"]))
    assert response.content == (VALID_HXML + "<!--comment-->").encode()


@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 100}})
def test_content_iterator_limit_is_bounded_and_preserves_previous_body() -> None:
    """The content iterator is read only up to the limit and the old body stays."""
    response = HyperviewResponse(VALID_HXML)
    consumed = []

    def chunks() -> Iterator[str]:
        for index in range(1000):
            consumed.append(index)
            yield "x" * 50

    with pytest.raises(TemplateValidationError) as error:
        response.content = chunks()
    assert error.value.code == "max_bytes"
    assert len(consumed) == 3
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize("status", [204, 205, 304])
def test_only_bodyless_statuses_accept_empty_and_reject_nonempty(status: int) -> None:
    """Only bodyless statuses accept empty content and reject non-empty content.

    Args:
        status: Bodyless status code under test.
    """
    response = HyperviewResponse(status=status)
    assert response.content == b""
    with pytest.raises(TemplateValidationError):
        response.content = VALID_HXML
    assert response.content == b""
    with pytest.raises(TemplateValidationError):
        HyperviewResponse(VALID_HXML, status=status)


@pytest.mark.parametrize(
    "read", [lambda response: response.content, lambda response: list(response), bytes]
)
def test_status_body_coherence_is_checked_before_read_or_emission(
    read: Callable[[HyperviewResponse], object],
) -> None:
    """Status and body coherence is checked before any read or emission.

    Args:
        read: Function that reads the response in one particular way.
    """
    response = HyperviewResponse(VALID_HXML)
    response.status_code = 204
    with pytest.raises(TemplateValidationError):
        read(response)
    response.status_code = 200
    assert response.content == VALID_HXML.encode()
    empty = HyperviewResponse(status=204)
    empty.status_code = 200
    with pytest.raises(TemplateValidationError):
        read(empty)


@pytest.mark.parametrize(
    "response_type", [HyperviewTemplateResponse, HyperviewFragmentTemplateResponse]
)
def test_lazy_response_retains_unrendered_state_when_final_validation_fails(
    response_type: type[HyperviewTemplateResponse],
) -> None:
    """A lazy response keeps its unrendered state when final validation fails.

    Args:
        response_type: Response class under test.
    """
    from django.template import engines

    with override_settings(TEMPLATES=TEMPLATES):
        response = response_type(
            None,
            engines["django"].from_string(
                '<view xmlns="https://hyperview.org/hyperview" typo="x"/>'
            ),
        )
        with pytest.raises(ContentNotRenderedError):
            response.write("x")
        for _ in range(2):
            with pytest.raises(TemplateValidationError):
                response.render()
            assert response.is_rendered is False
            with pytest.raises(ContentNotRenderedError):
                _ = response.content


@pytest.mark.parametrize(
    "response_type", [HyperviewTemplateResponse, HyperviewFragmentTemplateResponse]
)
def test_lazy_response_callbacks_cannot_install_invalid_hyperview_body(
    response_type: type[HyperviewTemplateResponse],
) -> None:
    """Lazy response callbacks cannot install an invalid Hyperview body.

    Args:
        response_type: Response class under test.
    """
    from django.template import engines

    with override_settings(TEMPLATES=TEMPLATES):
        response = response_type(None, engines["django"].from_string(VALID_HXML))
        response.add_post_render_callback(
            lambda current: setattr(current, "content", "<invalid>")
        )
        with pytest.raises(TemplateValidationError):
            response.render()
        assert response.content == VALID_HXML.encode()
        assert response.render() is response


@pytest.mark.parametrize("status", [204, 205, 304])
def test_lazy_bodyless_response_renders_once_and_rejects_nonempty(status: int) -> None:
    """A lazy bodyless response renders once and rejects non-empty content.

    Args:
        status: Bodyless status code under test.
    """

    class Template:
        calls = 0

        def render(self, context: object, request: object) -> str:
            self.calls += 1
            return ""

    template = Template()
    response = HyperviewTemplateResponse(None, template, status=status)
    assert response.render().content == b""
    assert response.render() is response
    assert template.calls == 1
    template.render = lambda context, request: VALID_HXML
    invalid = HyperviewTemplateResponse(None, template, status=status)
    with pytest.raises(TemplateValidationError):
        invalid.render()
    assert invalid.is_rendered is False


def test_lazy_callback_replacement_preserves_django_semantics() -> None:
    """A lazy callback that replaces the response keeps Django semantics."""
    from django.http import HttpResponse
    from django.template import engines

    replacement = HttpResponse("outside Hyperview guarantee", content_type="text/plain")
    with override_settings(TEMPLATES=TEMPLATES):
        response = HyperviewTemplateResponse(
            None, engines["django"].from_string(VALID_HXML)
        )
        seen = []
        response.add_post_render_callback(lambda current: replacement)
        response.add_post_render_callback(lambda current: seen.append(current))
        assert response.render() is replacement
        assert seen == [replacement]
        assert response.render() is response


def test_head_request_does_not_bypass_final_validation() -> None:
    """A HEAD request does not bypass the final validation."""
    from django.template import engines

    with override_settings(TEMPLATES=TEMPLATES):
        response = HyperviewTemplateResponse(
            RequestFactory().head("/"), engines["django"].from_string("<broken>")
        )
        with pytest.raises(TemplateValidationError):
            response.render()


def test_django_client_head_suppresses_only_after_lazy_document_validation() -> None:
    """The Django client drops a HEAD body only after validating the document."""
    from django.template import engines
    from django.test import Client
    from django.urls import path

    def view(request: HttpRequest) -> HttpResponse:
        return HyperviewTemplateResponse(
            request, engines["django"].from_string(VALID_HXML)
        )

    with override_settings(TEMPLATES=TEMPLATES, ROOT_URLCONF=__name__):
        globals()["urlpatterns"] = [path("head/", view)]
        try:
            response = Client().head("/head/")
            assert response.status_code == 200
            assert response.is_rendered
            assert response.content == b""
        finally:
            del globals()["urlpatterns"]


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
def test_eager_head_context_keeps_validated_document_before_transport_suppression(
    response_type: type[HyperviewResponse],
) -> None:
    """An eager HEAD response is validated before the transport suppresses its body.

    Args:
        response_type: Response class under test.
    """
    request = RequestFactory().head("/")
    response = response_type(VALID_HXML, request=request)
    response["Content-Length"] = len(response.content)
    response.content = b""
    assert response.content == b""
    assert list(response) == [b""]
    assert response["Content-Length"] == str(len(VALID_HXML.encode()))
    assert b"".join(response._container) == VALID_HXML.encode()
    for mutate in (
        lambda: setattr(response, "content", "<invalid>"),
        lambda: response.write("<invalid>"),
        lambda: response.writelines(["<invalid", ">"]),
    ):
        with pytest.raises(TemplateValidationError):
            mutate()
        assert response.content == b""
        assert b"".join(response._container) == VALID_HXML.encode()
    response.writelines(["<!--", "valid", "-->"])
    assert response.content == b""
    assert b"".join(response._container) == (VALID_HXML + "<!--valid-->").encode()
    response.status_code = 204
    with pytest.raises(TemplateValidationError):
        _ = response.content


@pytest.mark.parametrize("http_request", [None, RequestFactory().get("/")])
def test_empty_200_setter_without_head_context_remains_strict(
    http_request: HttpRequest | None,
) -> None:
    """An empty 200 body stays rejected without a HEAD request context.

    Args:
        http_request: Request passed to the response, or None for no request.
    """
    response = HyperviewResponse(VALID_HXML, request=http_request)
    with pytest.raises(TemplateValidationError):
        response.content = b""
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize("content", [b"", "<invalid>"])
def test_head_constructor_must_first_validate_a_nonempty_document(
    content: bytes | str,
) -> None:
    """A HEAD constructor must first validate a non-empty document.

    Args:
        content: Content passed to the constructor of the HEAD response.
    """
    with pytest.raises(TemplateValidationError):
        HyperviewResponse(content, request=RequestFactory().head("/"))


def test_cached_text_cannot_bypass_status_body_coherence() -> None:
    """Cached text cannot bypass the status and body coherence check."""
    response = HyperviewResponse(VALID_HXML)
    assert response.text == VALID_HXML
    response.status_code = 204
    with pytest.raises(TemplateValidationError):
        _ = response.text


@pytest.mark.parametrize("mutation", ["charset", "header"])
def test_response_read_rejects_mutated_non_utf8_metadata(mutation: str) -> None:
    """Reading rejects metadata that was mutated away from UTF-8.

    Args:
        mutation: Metadata that is mutated away from UTF-8, the charset or the header.
    """
    response = HyperviewResponse(VALID_HXML)
    if mutation == "charset":
        response.charset = "latin1"
    else:
        response["Content-Type"] = "application/xml; charset=latin1"
    with pytest.raises(ValueError, match="UTF-8"):
        bytes(response)


@pytest.mark.parametrize(
    "content",
    [
        memoryview(VALID_HXML.encode()),
        iter([VALID_HXML[:10], VALID_HXML[10:].encode()]),
    ],
)
def test_eager_response_accepts_bounded_utf8_candidate_forms(content: object) -> None:
    """An eager response accepts bounded UTF-8 candidate forms.

    Args:
        content: Content passed in a supported form such as a memoryview or an iterator.
    """
    assert HyperviewResponse(content).content == VALID_HXML.encode()


def test_iterator_failure_closes_input_and_preserves_committed_bytes() -> None:
    """A failing iterator is closed and the committed bytes are kept."""
    response = HyperviewResponse(VALID_HXML)
    closed = []

    def chunks() -> Iterator[str]:
        try:
            yield VALID_HXML
            raise RuntimeError("producer failure")
        finally:
            closed.append(True)

    with pytest.raises(RuntimeError, match="producer failure"):
        response.content = chunks()
    assert closed == [True]
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize("content", ["\ud800", b"\xff", 12])
def test_invalid_candidate_encoding_or_scalar_preserves_committed_body(
    content: object,
) -> None:
    """An invalid candidate encoding or scalar keeps the committed body.

    Args:
        content: Candidate content that is rejected.
    """
    response = HyperviewResponse(VALID_HXML)
    with pytest.raises(TemplateValidationError):
        response.content = content
    assert response.content == VALID_HXML.encode()


def test_multibyte_content_limit_counts_utf8_bytes() -> None:
    """The multibyte content limit counts UTF-8 bytes."""
    response = HyperviewResponse(VALID_HXML)
    candidate = '<text xmlns="https://hyperview.org/hyperview">éé</text>'
    with override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": len(candidate)}}):
        with pytest.raises(TemplateValidationError) as error:
            response.content = candidate
    assert error.value.code == "max_bytes"
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize(
    "content_type,charset",
    [(None, "unknown-codec"), ("application/xml; charset=unknown-codec", None)],
)
def test_unknown_response_encoding_is_rejected(
    content_type: str | None, charset: str | None
) -> None:
    """An unknown response encoding is rejected.

    Args:
        content_type: Content-Type header value, or None to omit it.
        charset: Charset argument passed to the constructor, or None.
    """
    with pytest.raises(ValueError, match="UTF-8"):
        HyperviewResponse(VALID_HXML, content_type=content_type, charset=charset)


@pytest.mark.parametrize("name", ["utf-8\x00", "\x00", "utf-8\U0000d800"])
@pytest.mark.parametrize("where", ["argument", "content_type"])
def test_unlookupable_response_encoding_is_the_same_typed_failure(
    where: str, name: str
) -> None:
    """codecs.lookup raises ValueError, not LookupError, for NUL and a surrogate.

    Args:
        where: Where the charset is given, as the argument or inside the content type.
        name: Charset name that codecs cannot look up.
    """
    options = (
        {"charset": name}
        if where == "argument"
        else {"content_type": f"application/xml; charset={name}"}
    )

    with pytest.raises(ValueError) as error:
        HyperviewResponse(VALID_HXML, **options)

    assert str(error.value) == "Hyperview responses require UTF-8"


@pytest.mark.parametrize("where", ["argument", "content_type"])
def test_distinct_charset_names_never_grow_the_codec_cache(where: str) -> None:
    """A charset is decided from the alias table, not remembered by codecs.lookup.

    Args:
        where: Where the charset is given, as the argument or inside the content type.
    """
    before = len(encodings._cache)
    unique = uuid.uuid4().hex

    for index in range(200):
        name = f"zz{unique}{index}"
        options = (
            {"charset": name}
            if where == "argument"
            else {"content_type": f"application/xml; charset={name}"}
        )
        with pytest.raises(ValueError, match="UTF-8"):
            HyperviewResponse(VALID_HXML, **options)

    assert len(encodings._cache) == before


@pytest.mark.parametrize("name", ["UTF-8", "utf8", "U8", "utf_8", "cp65001"])
def test_every_spelling_of_utf8_is_still_a_valid_response_charset(name: str) -> None:
    """Every spelling of UTF-8 is still a valid response charset.

    Args:
        name: Spelling of the UTF-8 charset name.
    """
    response = HyperviewResponse(
        VALID_HXML, content_type=f"application/xml; charset={name}"
    )

    assert response.charset == "utf-8"


def test_overridden_rendered_content_cannot_bypass_final_validation() -> None:
    """An overridden rendered content cannot bypass the final validation."""

    class CustomResponse(HyperviewTemplateResponse):
        @property
        def rendered_content(self) -> str:
            return "<invalid>"

    response = CustomResponse(None, "unused.xml")
    with pytest.raises(TemplateValidationError):
        response.render()
    assert not response.is_rendered


def test_django_client_eager_head_validates_then_suppresses_transport() -> None:
    """The Django client validates an eager HEAD response before it drops the body."""
    from django.test import Client
    from django.urls import path

    def view(request: HttpRequest) -> HttpResponse:
        return HyperviewResponse(VALID_HXML, request=request)

    def invalid(request: HttpRequest) -> HttpResponse:
        return HyperviewResponse("<invalid>", request=request)

    with override_settings(ROOT_URLCONF=__name__):
        globals()["urlpatterns"] = [path("head/", view), path("invalid/", invalid)]
        try:
            assert Client().head("/head/").content == b""
            with pytest.raises(TemplateValidationError):
                Client().head("/invalid/")
        finally:
            del globals()["urlpatterns"]


GZIP_HXML = (
    '<view xmlns="https://hyperview.org/hyperview"><text>'
    + "ordinary output " * 100
    + "</text></view>"
)


@pytest.mark.parametrize(
    "response_type",
    [
        HyperviewResponse,
        HyperviewFragmentResponse,
        HyperviewTemplateResponse,
        HyperviewFragmentTemplateResponse,
    ],
)
def test_django_gzip_middleware_encodes_only_a_validated_representation(
    response_type: type[HyperviewResponse] | type[HyperviewTemplateResponse],
) -> None:
    """Django's gzip middleware encodes only a validated representation.

    Args:
        response_type: Response class under test.
    """
    import gzip

    from django.middleware.gzip import GZipMiddleware
    from django.template import engines

    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    if issubclass(response_type, HyperviewTemplateResponse):
        with override_settings(TEMPLATES=TEMPLATES):
            response = response_type(request, engines["django"].from_string(GZIP_HXML))
            response.render()
    else:
        response = response_type(GZIP_HXML, request=request)
    response["ETag"] = '"strong"'
    encoded = GZipMiddleware(lambda request: response).process_response(
        request, response
    )
    assert encoded is response
    assert response["Content-Encoding"] == "gzip"
    assert response["Content-Length"] == str(len(response.content))
    assert response["ETag"] == 'W/"strong"'
    assert "Accept-Encoding" in response["Vary"]
    assert gzip.decompress(response.content) == GZIP_HXML.encode()
    assert b"".join(response) == response.content
    assert bytes(response).endswith(response.content)


def test_gzip_transport_requires_matching_header_at_every_emission_boundary() -> None:
    """Gzip transport needs a matching header at every emission boundary."""
    import gzip

    response = HyperviewResponse(GZIP_HXML)
    encoded = gzip.compress(GZIP_HXML.encode())
    response.content = encoded
    assert (
        response.content == encoded
    )  # Django rereads length before adding its header.
    for emit in (list, bytes, lambda current: current.serialize()):
        with pytest.raises(TemplateValidationError):
            emit(response)
    response["Content-Encoding"] = "gzip"
    assert b"".join(response) == encoded
    response["Content-Encoding"] = "br"
    with pytest.raises(TemplateValidationError):
        bytes(response)


@pytest.mark.parametrize("encoding", ["br", "zstd", "deflate", "gzip, br"])
def test_non_gzip_content_encoding_is_reported_as_unsupported(encoding: str) -> None:
    """The message names the real limit instead of suggesting a transport mismatch.

    Args:
        encoding: Unsupported Content-Encoding header value.
    """
    response = HyperviewResponse(VALID_HXML, headers={"Content-Encoding": encoding})

    with pytest.raises(TemplateValidationError, match="only gzip") as error:
        bytes(response)
    assert error.value.code == "http_encoding"
    assert encoding not in str(error.value)


def test_gzip_header_without_a_gzip_body_keeps_the_mismatch_message() -> None:
    """A gzip header without a gzip body keeps the mismatch message."""
    response = HyperviewResponse(VALID_HXML, headers={"Content-Encoding": "gzip"})

    with pytest.raises(TemplateValidationError, match="does not match"):
        bytes(response)


@pytest.mark.parametrize(
    "kind", ["different", "truncated", "trailing", "members", "bomb", "corrupt"]
)
def test_gzip_candidate_must_be_exact_complete_and_bounded(kind: str) -> None:
    """A gzip candidate must be exact, complete and bounded.

    Args:
        kind: Way the candidate differs from a valid gzip body.
    """
    import gzip

    encoded = gzip.compress(GZIP_HXML.encode())
    candidates = {
        "different": gzip.compress(VALID_HXML.encode()),
        "truncated": encoded[:-1],
        "trailing": encoded + b"extra",
        "members": encoded + encoded,
        "bomb": gzip.compress(b"x" * 1_000_000),
        "corrupt": encoded[:-8] + bytes([encoded[-8] ^ 1]) + encoded[-7:],
    }
    response = HyperviewResponse(GZIP_HXML)
    with pytest.raises(TemplateValidationError):
        response.content = candidates[kind]
    assert response.content == GZIP_HXML.encode()


def test_gzip_constructor_is_not_a_validation_bypass() -> None:
    """The gzip constructor path is not a validation bypass."""
    import gzip

    with pytest.raises(TemplateValidationError):
        HyperviewResponse(
            gzip.compress(GZIP_HXML.encode()), headers={"Content-Encoding": "gzip"}
        )


@pytest.mark.parametrize("mutation", ["content", "write", "writelines"])
def test_document_mutation_after_gzip_requires_header_removal_and_is_atomic(
    mutation: str,
) -> None:
    """Mutating the document after gzip needs header removal and stays atomic.

    Args:
        mutation: Way the document is mutated, as content, write or writelines.
    """
    import gzip

    response = HyperviewResponse(GZIP_HXML)
    encoded = gzip.compress(GZIP_HXML.encode())
    response.content = encoded
    response["Content-Encoding"] = "gzip"
    response["Content-Length"] = len(encoded)
    with pytest.raises(TemplateValidationError):
        if mutation == "content":
            response.content = VALID_HXML
        elif mutation == "write":
            response.write("<!--valid-->")
        else:
            response.writelines(["<!--", "valid", "-->"])
    assert response.content == encoded
    del response["Content-Encoding"]
    with pytest.raises(TemplateValidationError):
        response.content = "<invalid>"
    assert response.content == encoded
    response.content = VALID_HXML
    assert response.content == VALID_HXML.encode()
    assert "Content-Length" not in response
    assert b"".join(response) == VALID_HXML.encode()


def test_lazy_callback_can_gzip_then_head_suppresses_only_transport() -> None:
    """A lazy callback can gzip, and HEAD then suppresses only the transport."""
    import gzip

    from django.middleware.gzip import GZipMiddleware
    from django.template import engines

    request = RequestFactory().head("/", HTTP_ACCEPT_ENCODING="gzip")
    with override_settings(TEMPLATES=TEMPLATES):
        response = HyperviewTemplateResponse(
            request, engines["django"].from_string(GZIP_HXML)
        )
        response.add_post_render_callback(
            lambda current: GZipMiddleware(lambda request: current).process_response(
                request, current
            )
        )
        assert response.render() is response
        assert gzip.decompress(response.content) == GZIP_HXML.encode()
        length = response["Content-Length"]
        response.content = b""
        assert b"".join(response) == b""
        assert response["Content-Length"] == length
        assert response.render() is response


@pytest.mark.parametrize("status", [204, 205, 304])
@pytest.mark.parametrize("encoding", ["gzip", "br"])
def test_bodyless_status_preserves_representation_encoding_metadata(
    status: int, encoding: str
) -> None:
    """A bodyless status keeps the representation encoding metadata.

    Args:
        status: Bodyless status code under test.
        encoding: Content-Encoding header value that is kept as metadata.
    """
    response = HyperviewResponse(
        status=status, headers={"Content-Encoding": encoding, "ETag": '"v1"'}
    )
    assert response.content == b""
    assert list(response) == [b""]
    assert bytes(response).endswith(b"\r\n\r\n")
    assert response["Content-Encoding"] == encoding
    with pytest.raises(TemplateValidationError):
        response.content = VALID_HXML


@pytest.mark.parametrize("method", ["get", "head"])
def test_nonbodyless_response_encoding_must_still_match_transport(method: str) -> None:
    """A non-bodyless response must still match its encoding to the transport.

    Args:
        method: Lower-case HTTP method the request uses.
    """
    request = getattr(RequestFactory(), method)("/")
    response = HyperviewResponse(
        VALID_HXML, request=request, headers={"Content-Encoding": "gzip"}
    )
    if method == "head":
        response.content = b""
    with pytest.raises(TemplateValidationError):
        bytes(response)
