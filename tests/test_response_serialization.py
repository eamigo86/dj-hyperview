"""A rendered response stays picklable, copyable and cacheable.

Django serializes a response to store it: cache_page writes it to the cache,
and copy.deepcopy and pickle copy it. The response keeps the private
validated result for the gzip handoff, so that result must not carry the settings
snapshot: HYPERVIEW["SOURCES"] options are read-only mappings that cannot be
pickled, and they may hold credentials that must not reach a shared cache entry.
"""

import copy
import gzip
import pickle
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse
from django.middleware.gzip import GZipMiddleware
from django.test import RequestFactory, override_settings
from django.views.decorators.cache import cache_page

from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import (
    HyperviewFragmentResponse,
    HyperviewFragmentTemplateResponse,
    HyperviewResponse,
    HyperviewTemplateResponse,
)
from tests.response_documents import DOCUMENT, FRAGMENT, STRICT_XSD, TEMPLATES

SECRET = "credential-that-must-not-be-cached"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "response-serialization",
    }
}


def eager(**kwargs: Any) -> HyperviewResponse:
    """Build an eager response.

    Args:
        **kwargs: Keyword arguments passed to the response class.

    Returns:
        A response that holds a document.
    """
    return HyperviewResponse(DOCUMENT, **kwargs)


def eager_fragment(**kwargs: Any) -> HyperviewFragmentResponse:
    """Build an eager fragment response.

    Args:
        **kwargs: Keyword arguments passed to the response class.

    Returns:
        A response that holds a fragment.
    """
    return HyperviewFragmentResponse(FRAGMENT, **kwargs)


def lazy(**kwargs: Any) -> HyperviewTemplateResponse:
    """Build a lazy template response.

    Args:
        **kwargs: Keyword arguments passed to the response class.

    Returns:
        A response that renders a template when it is asked.
    """
    return HyperviewTemplateResponse(
        RequestFactory().get("/"), "screen.xml", using="django", **kwargs
    )


def lazy_fragment(**kwargs: Any) -> HyperviewFragmentTemplateResponse:
    """Build a lazy fragment template response.

    Args:
        **kwargs: Keyword arguments passed to the response class.

    Returns:
        A response that renders a fragment template when it is asked.
    """
    return HyperviewFragmentTemplateResponse(
        RequestFactory().get("/"), "fragment.xml", using="django", **kwargs
    )


KINDS = {
    "response": (eager, DOCUMENT),
    "fragment_response": (eager_fragment, FRAGMENT),
    "template_response": (lazy, DOCUMENT),
    "fragment_template_response": (lazy_fragment, FRAGMENT),
}
kinds = pytest.mark.parametrize(("make", "document"), KINDS.values(), ids=list(KINDS))
# Only a document that uses the extra schema is rejected when that schema changes.
documents = pytest.mark.parametrize(
    ("make", "document"),
    [KINDS["response"], KINDS["template_response"]],
    ids=["response", "template_response"],
)


@pytest.fixture
def sources(extra_schema: Path) -> Iterator[Path]:
    """Configure a source whose read-only options hold a credential.

    Args:
        extra_schema: Path of the extra schema that the settings load.

    Yields:
        The extra schema, while the source settings are active.
    """
    hyperview = {
        "EXTRA_SCHEMAS": [extra_schema],
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": DOCUMENT, "revision": SECRET},
            }
        ],
    }
    with override_settings(TEMPLATES=TEMPLATES, HYPERVIEW=hyperview, CACHES=CACHES):
        caches["default"].clear()
        yield extra_schema
        caches["default"].clear()


def rendered(make: Callable[[], HttpResponse]) -> HttpResponse:
    """Build a response and render it if it is lazy.

    Args:
        make: Factory that builds the response under test.

    Returns:
        The response, rendered when it supports rendering.
    """
    response = make()
    if hasattr(response, "render"):
        response.render()
    return response


def through_gzip(response: HttpResponse) -> HttpResponse:
    """Pass a response through the gzip middleware.

    Args:
        response: Response to hand to the gzip middleware.

    Returns:
        The same response, after the gzip middleware ran.
    """
    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    GZipMiddleware(lambda request: response).process_response(request, response)
    return response


@kinds
def test_response_can_be_pickled(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Response can be pickled.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """
    response = rendered(make)
    payload = pickle.dumps(response)
    restored = pickle.loads(payload)
    assert restored.content == document.encode()
    assert restored.status_code == response.status_code
    assert restored["Content-Type"] == response["Content-Type"]
    assert type(restored) is type(response)


@kinds
def test_response_can_be_deep_copied(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Response can be deep copied.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """
    response = rendered(make)
    duplicate = copy.deepcopy(response)
    assert duplicate.content == document.encode()
    assert type(duplicate) is type(response)
    assert duplicate is not response


@kinds
def test_pickled_response_does_not_carry_the_settings(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Pickled response does not carry the settings.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """
    payload = pickle.dumps(rendered(make))
    assert SECRET.encode() not in payload
    assert b"HyperviewSettings" not in payload
    assert b"SourceSettings" not in payload


@kinds
def test_restored_response_still_passes_the_gzip_handoff(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Restored response still passes the gzip handoff.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """
    restored = pickle.loads(pickle.dumps(rendered(make)))
    through_gzip(restored)
    assert restored["Content-Encoding"] == "gzip"
    assert gzip.decompress(restored.content) == document.encode()


@documents
@pytest.mark.parametrize(
    "copier", [lambda r: pickle.loads(pickle.dumps(r)), copy.deepcopy]
)
def test_restored_response_gets_the_full_check_at_the_gzip_handoff(
    sources: Path,
    make: Callable[[], HttpResponse],
    document: str,
    copier: Callable[[HttpResponse], HttpResponse],
) -> None:
    """Restored response gets the full check at the gzip handoff.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
        copier: Function that copies a response.
    """
    restored = copier(rendered(make))
    # The body has waited, so it is validated against the registry current now.
    sources.write_text(STRICT_XSD, encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        through_gzip(restored)
    assert error.value.code == "schema"
    assert "Content-Encoding" not in restored
    assert restored.content == document.encode()


@kinds
def test_cache_page_stores_and_serves_the_response(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Cache page stores and serves the response.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """
    calls = []

    @cache_page(60)
    def view(request: HttpRequest) -> HttpResponse:
        calls.append(request.path)
        return make()

    first = view(RequestFactory().get("/screen"))
    if hasattr(first, "render"):
        first.render()
    assert first.content == document.encode()
    second = view(RequestFactory().get("/screen"))
    if hasattr(second, "render"):
        second.render()
    assert calls == ["/screen"]
    assert second.content == document.encode()
    assert type(second) is type(first)
    assert second is not first


@kinds
def test_cached_response_still_passes_the_gzip_handoff(
    sources: Path, make: Callable[[], HttpResponse], document: str
) -> None:
    """Cached response still passes the gzip handoff.

    Args:
        sources: Path of the extra schema that the configured source settings load.
        make: Factory that builds the response under test.
        document: Document that the response holds.
    """

    @cache_page(60)
    def view(request: HttpRequest) -> HttpResponse:
        return make()

    first = view(RequestFactory().get("/screen"))
    if hasattr(first, "render"):
        first.render()
    cached = view(RequestFactory().get("/screen"))
    through_gzip(cached)
    assert cached["Content-Encoding"] == "gzip"
    assert gzip.decompress(cached.content) == document.encode()
