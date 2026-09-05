import pytest
from django.core.exceptions import ImproperlyConfigured
from django.template import TemplateDoesNotExist
from django.template.response import ContentNotRenderedError
from django.test import RequestFactory, override_settings

import dj_hyperview
from dj_hyperview.http import (
    HYPERVIEW_MEDIA_TYPE,
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
                    {"screen.xml": "<view>{{ title }}</view>"},
                )
            ]
        },
    }
]


@override_settings(DEFAULT_CHARSET="iso-8859-1")
def test_hyperview_response_preserves_http_contract():
    response = HyperviewResponse(
        "<view>Accepted</view>",
        status=202,
        headers={"X-Screen": "accepted"},
    )

    assert response.content == b"<view>Accepted</view>"
    assert response.charset == "utf-8"
    assert response.status_code == 202
    assert response.headers["X-Screen"] == "accepted"
    assert response.headers["Content-Type"] == f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"


def test_hyperview_response_preserves_explicit_content_type_and_charset():
    response = HyperviewResponse(
        "olá",
        content_type="application/xml",
        charset="iso-8859-1",
        status=207,
        reason="Multi-Status",
    )

    assert response.content == "olá".encode("iso-8859-1")
    assert response.charset == "iso-8859-1"
    assert response.status_code == 207
    assert response.reason_phrase == "Multi-Status"
    assert response.headers["Content-Type"] == "application/xml"


@override_settings(TEMPLATES=TEMPLATES, DEFAULT_CHARSET="iso-8859-1")
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
    assert response.content == b"<view>A &amp; B</view>"
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


@override_settings(TEMPLATES=TEMPLATES)
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

    assert response.content == b"<view>Consumer screen</view>"
    assert response.headers["Content-Type"] == f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"


@override_settings(TEMPLATES=TEMPLATES)
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
    assert response.render().content == b"<view></view>"


def test_template_view_requires_a_consumer_template_name():
    view = HyperviewTemplateView.as_view()

    with pytest.raises(ImproperlyConfigured, match="requires either a definition"):
        view(RequestFactory().get("/missing-template"))


def test_http_api_is_available_from_the_package_namespace():
    assert dj_hyperview.HYPERVIEW_MEDIA_TYPE == "application/vnd.hyperview+xml"
    assert dj_hyperview.HyperviewResponse is HyperviewResponse
    assert dj_hyperview.HyperviewTemplateResponse is HyperviewTemplateResponse
    assert dj_hyperview.HyperviewTemplateView is HyperviewTemplateView
