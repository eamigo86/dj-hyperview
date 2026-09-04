"""HTTP acceptance tests for generic consumer-owned Hyperview documents."""

from pathlib import Path

import pytest
from django.core.cache import CacheHandler
from django.db.backends.base.base import BaseDatabaseWrapper
from django.template.response import ContentNotRenderedError
from django.test import Client, override_settings
from django.test.client import RequestFactory

from dj_hyperview import HYPERVIEW_MEDIA_TYPE, HyperviewTemplateResponse
from tests.consumer_project import settings_filesystem as filesystem
from tests.consumer_project.views import ConsumerFullDocumentView, consumer_fragment

HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
}


@override_settings(**HTTP_SETTINGS)
def test_full_document_preserves_context_and_http_metadata(client: Client) -> None:
    """The full endpoint returns escaped UTF-8 HXML and custom metadata."""
    response = client.get("/documents/full/", {"title": "Café & <consumer>"})

    assert response.status_code == 201
    assert response.headers["Content-Type"] == HYPERVIEW_MEDIA_TYPE
    assert response.headers["X-Consumer-Document"] == "full"
    assert response.charset == "utf-8"
    assert response.template_name == ["screens/full.xml"]
    assert response.context_data["title"] == "Café & <consumer>"
    assert response.content.decode() == (
        "<view><header>primary-layout</header>"
        "<text>primary: Café &amp; &lt;consumer&gt;</text></view>"
    )


@override_settings(**HTTP_SETTINGS)
def test_fragment_uses_public_engine_response_and_canonical_name(
    client: Client,
) -> None:
    """The fragment endpoint renders its canonical consumer template."""
    response = client.get("/documents/fragment/", {"label": "Niño & <fragment>"})

    assert response.status_code == 206
    assert response.headers["Content-Type"] == HYPERVIEW_MEDIA_TYPE
    assert response.headers["X-Consumer-Document"] == "fragment"
    assert response.headers["X-Hyperview-Template"] == "fragments/item.xml"
    assert response.content.decode() == (
        "<view><text>Niño &amp; &lt;fragment&gt;</text></view>"
    )


@override_settings(**HTTP_SETTINGS)
def test_unknown_document_route_returns_a_redacted_django_404(
    client: Client,
) -> None:
    """Unknown consumer routes do not disclose filesystem template origins."""
    response = client.get("/documents/unknown/")

    assert response.status_code == 404
    assert str(filesystem.FIXTURE_ROOT).encode() not in response.content
    assert b"<view>" not in response.content


def test_full_document_defers_template_resolution_until_render(tmp_path: Path) -> None:
    """The class-based endpoint remains lazy until Django renders its response."""
    settings = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    }
    request = RequestFactory().get("/documents/full/", {"title": "Late & <safe>"})

    with override_settings(HYPERVIEW=settings):
        response = ConsumerFullDocumentView.as_view()(request)
        assert isinstance(response, HyperviewTemplateResponse)
        assert response.is_rendered is False
        with pytest.raises(ContentNotRenderedError):
            _ = response.content

        screen = tmp_path / "screens" / "full.xml"
        screen.parent.mkdir()
        screen.write_text("<view>{{ title }}</view>", encoding="utf-8")
        response.render()

    assert response.content.decode() == "<view>Late &amp; &lt;safe&gt;</view>"
    assert response.status_code == 201
    assert response.headers["X-Consumer-Document"] == "full"


@pytest.mark.parametrize("path", ["/documents/full/", "/documents/fragment/"])
@override_settings(**HTTP_SETTINGS)
def test_document_endpoints_reject_unsupported_methods(
    client: Client, path: str
) -> None:
    """Generic document endpoints reject mutations outside their GET contract."""
    response = client.post(path)

    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]


@override_settings(**HTTP_SETTINGS)
def test_documents_do_not_touch_optional_database_or_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem HTTP rendering stays independent from optional infrastructure."""

    def _forbidden_optional_access(*args: object, **kwargs: object) -> None:
        raise AssertionError("document endpoint accessed optional infrastructure")

    monkeypatch.setattr(CacheHandler, "__getitem__", _forbidden_optional_access)
    monkeypatch.setattr(BaseDatabaseWrapper, "cursor", _forbidden_optional_access)

    full = ConsumerFullDocumentView.as_view()(RequestFactory().get("/documents/full/"))
    full.render()
    fragment = consumer_fragment(RequestFactory().get("/documents/fragment/"))

    assert full.status_code == 201
    assert fragment.status_code == 206
    assert full.content.startswith(b"<view>")
    assert fragment.content.startswith(b"<view>")
