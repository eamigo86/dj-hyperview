"""A rendered response stays picklable, copyable and cacheable.

Django serializes a response to store it: ``cache_page`` writes it to the cache,
and ``copy.deepcopy`` and ``pickle`` copy it. The response keeps the private
validated result for the gzip handoff, so that result must not carry the settings
snapshot: ``HYPERVIEW["SOURCES"]`` options are read-only mappings that cannot be
pickled, and they may hold credentials that must not reach a shared cache entry.
"""

import copy
import gzip
import pickle

import pytest
from django.core.cache import caches
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


def eager(**kwargs):
    return HyperviewResponse(DOCUMENT, **kwargs)


def eager_fragment(**kwargs):
    return HyperviewFragmentResponse(FRAGMENT, **kwargs)


def lazy(**kwargs):
    return HyperviewTemplateResponse(
        RequestFactory().get("/"), "screen.xml", using="django", **kwargs
    )


def lazy_fragment(**kwargs):
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
def sources(extra_schema):
    """Configure a source whose read-only options hold a credential."""
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


def rendered(make):
    response = make()
    if hasattr(response, "render"):
        response.render()
    return response


def through_gzip(response):
    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    GZipMiddleware(lambda request: response).process_response(request, response)
    return response


@kinds
def test_response_can_be_pickled(sources, make, document):
    response = rendered(make)
    payload = pickle.dumps(response)
    restored = pickle.loads(payload)
    assert restored.content == document.encode()
    assert restored.status_code == response.status_code
    assert restored["Content-Type"] == response["Content-Type"]
    assert type(restored) is type(response)


@kinds
def test_response_can_be_deep_copied(sources, make, document):
    response = rendered(make)
    duplicate = copy.deepcopy(response)
    assert duplicate.content == document.encode()
    assert type(duplicate) is type(response)
    assert duplicate is not response


@kinds
def test_pickled_response_does_not_carry_the_settings(sources, make, document):
    payload = pickle.dumps(rendered(make))
    assert SECRET.encode() not in payload
    assert b"HyperviewSettings" not in payload
    assert b"SourceSettings" not in payload


@kinds
def test_restored_response_still_passes_the_gzip_handoff(sources, make, document):
    restored = pickle.loads(pickle.dumps(rendered(make)))
    through_gzip(restored)
    assert restored["Content-Encoding"] == "gzip"
    assert gzip.decompress(restored.content) == document.encode()


@documents
@pytest.mark.parametrize(
    "copier", [lambda r: pickle.loads(pickle.dumps(r)), copy.deepcopy]
)
def test_restored_response_gets_the_full_check_at_the_gzip_handoff(
    sources, make, document, copier
):
    restored = copier(rendered(make))
    # The body has waited, so it is validated against the registry current now.
    sources.write_text(STRICT_XSD, encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        through_gzip(restored)
    assert error.value.code == "schema"
    assert "Content-Encoding" not in restored
    assert restored.content == document.encode()


@kinds
def test_cache_page_stores_and_serves_the_response(sources, make, document):
    calls = []

    @cache_page(60)
    def view(request):
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
def test_cached_response_still_passes_the_gzip_handoff(sources, make, document):
    @cache_page(60)
    def view(request):
        return make()

    first = view(RequestFactory().get("/screen"))
    if hasattr(first, "render"):
        first.render()
    cached = view(RequestFactory().get("/screen"))
    through_gzip(cached)
    assert cached["Content-Encoding"] == "gzip"
    assert gzip.decompress(cached.content) == document.encode()
