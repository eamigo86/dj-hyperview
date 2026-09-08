import pytest
from django.core.exceptions import ImproperlyConfigured
from django.template import TemplateDoesNotExist
from django.template.response import ContentNotRenderedError
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
def test_hyperview_response_preserves_http_contract():
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


def test_hyperview_response_preserves_explicit_content_type_and_utf8():
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
    """The media-type parameter cannot silently override the UTF-8 contract."""
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
        f"{HYPERVIEW_MEDIA_TYPE}; profile=mobile app; charset=utf-8"
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
    """Replacement fragments cannot contain client-owned document roots."""
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
def test_template_response_preserves_lazy_render_contract():
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
def test_template_response_defers_missing_template_error_until_render():
    response = HyperviewTemplateResponse(
        RequestFactory().get("/missing"),
        "missing.xml",
    )

    assert response.is_rendered is False
    with pytest.raises(TemplateDoesNotExist, match="missing.xml"):
        response.render()
    assert response.is_rendered is False


@override_settings(TEMPLATES=TEMPLATES, HYPERVIEW={})
def test_template_response_requires_explicit_django_engine_without_sources():
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
def test_template_view_renders_consumer_template_and_context():
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
def test_template_view_forwards_response_status_and_headers():
    class AcceptedView(HyperviewTemplateView):
        template_name = "screen.xml"

        def render_to_response(self, context, **response_kwargs):
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


def test_template_view_requires_a_consumer_template_name():
    view = HyperviewTemplateView.as_view()

    with pytest.raises(ImproperlyConfigured, match="requires either a definition"):
        view(RequestFactory().get("/missing-template"))


def test_http_api_is_available_from_the_package_namespace():
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
def test_eager_response_rejects_invalid_final_content(content):
    with pytest.raises(TemplateValidationError):
        HyperviewResponse(content)


def test_foreign_media_type_cannot_disable_hyperview_validation():
    with pytest.raises(TemplateValidationError):
        HyperviewResponse("not XML", content_type="text/plain")
    with pytest.raises(ValueError, match="UTF-8"):
        HyperviewResponse(VALID_HXML, content_type="application/xml", charset="latin1")


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("mutation", ["content", "write", "writelines"])
def test_invalid_response_mutations_are_atomic(response_type, mutation):
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


def test_writelines_assembles_once_before_validating():
    response = HyperviewResponse(VALID_HXML)
    response.writelines(iter(["<!--", "comment", "-->"]))
    assert response.content == (VALID_HXML + "<!--comment-->").encode()


@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 100}})
def test_content_iterator_limit_is_bounded_and_preserves_previous_body():
    response = HyperviewResponse(VALID_HXML)
    consumed = []

    def chunks():
        for index in range(1000):
            consumed.append(index)
            yield "x" * 50

    with pytest.raises(TemplateValidationError) as error:
        response.content = chunks()
    assert error.value.code == "max_bytes"
    assert len(consumed) == 3
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize("status", [204, 205, 304])
def test_only_bodyless_statuses_accept_empty_and_reject_nonempty(status):
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
def test_status_body_coherence_is_checked_before_read_or_emission(read):
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
    response_type,
):
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
def test_lazy_response_callbacks_cannot_install_invalid_hyperview_body(response_type):
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
def test_lazy_bodyless_response_renders_once_and_rejects_nonempty(status):
    class Template:
        calls = 0

        def render(self, context, request):
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


def test_lazy_callback_replacement_preserves_django_semantics():
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


def test_head_request_does_not_bypass_final_validation():
    from django.template import engines

    with override_settings(TEMPLATES=TEMPLATES):
        response = HyperviewTemplateResponse(
            RequestFactory().head("/"), engines["django"].from_string("<broken>")
        )
        with pytest.raises(TemplateValidationError):
            response.render()


def test_django_client_head_suppresses_only_after_lazy_document_validation():
    from django.template import engines
    from django.test import Client
    from django.urls import path

    def view(request):
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
    response_type,
):
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
def test_empty_200_setter_without_head_context_remains_strict(http_request):
    response = HyperviewResponse(VALID_HXML, request=http_request)
    with pytest.raises(TemplateValidationError):
        response.content = b""
    assert response.content == VALID_HXML.encode()


@pytest.mark.parametrize("content", [b"", "<invalid>"])
def test_head_constructor_must_first_validate_a_nonempty_document(content):
    with pytest.raises(TemplateValidationError):
        HyperviewResponse(content, request=RequestFactory().head("/"))


def test_cached_text_cannot_bypass_status_body_coherence():
    response = HyperviewResponse(VALID_HXML)
    assert response.text == VALID_HXML
    response.status_code = 204
    with pytest.raises(TemplateValidationError):
        _ = response.text


@pytest.mark.parametrize("mutation", ["charset", "header"])
def test_response_read_rejects_mutated_non_utf8_metadata(mutation):
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
def test_eager_response_accepts_bounded_utf8_candidate_forms(content):
    assert HyperviewResponse(content).content == VALID_HXML.encode()


def test_iterator_failure_closes_input_and_preserves_committed_bytes():
    response = HyperviewResponse(VALID_HXML)
    closed = []

    def chunks():
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
def test_invalid_candidate_encoding_or_scalar_preserves_committed_body(content):
    response = HyperviewResponse(VALID_HXML)
    with pytest.raises(TemplateValidationError):
        response.content = content
    assert response.content == VALID_HXML.encode()


def test_multibyte_content_limit_counts_utf8_bytes():
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
def test_unknown_response_encoding_is_rejected(content_type, charset):
    with pytest.raises(ValueError, match="UTF-8"):
        HyperviewResponse(VALID_HXML, content_type=content_type, charset=charset)


def test_overridden_rendered_content_cannot_bypass_final_validation():
    class CustomResponse(HyperviewTemplateResponse):
        @property
        def rendered_content(self):
            return "<invalid>"

    response = CustomResponse(None, "unused.xml")
    with pytest.raises(TemplateValidationError):
        response.render()
    assert not response.is_rendered


def test_django_client_eager_head_validates_then_suppresses_transport():
    from django.test import Client
    from django.urls import path

    def view(request):
        return HyperviewResponse(VALID_HXML, request=request)

    def invalid(request):
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
def test_django_gzip_middleware_encodes_only_a_validated_representation(response_type):
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


def test_gzip_transport_requires_matching_header_at_every_emission_boundary():
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


@pytest.mark.parametrize(
    "kind", ["different", "truncated", "trailing", "members", "bomb", "corrupt"]
)
def test_gzip_candidate_must_be_exact_complete_and_bounded(kind):
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


def test_gzip_constructor_is_not_a_validation_bypass():
    import gzip

    with pytest.raises(TemplateValidationError):
        HyperviewResponse(
            gzip.compress(GZIP_HXML.encode()), headers={"Content-Encoding": "gzip"}
        )


@pytest.mark.parametrize("mutation", ["content", "write", "writelines"])
def test_document_mutation_after_gzip_requires_header_removal_and_is_atomic(mutation):
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


def test_lazy_callback_can_gzip_then_head_suppresses_only_transport():
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
def test_bodyless_status_preserves_representation_encoding_metadata(status, encoding):
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
def test_nonbodyless_response_encoding_must_still_match_transport(method):
    request = getattr(RequestFactory(), method)("/")
    response = HyperviewResponse(
        VALID_HXML, request=request, headers={"Content-Encoding": "gzip"}
    )
    if method == "head":
        response.content = b""
    with pytest.raises(TemplateValidationError):
        bytes(response)
