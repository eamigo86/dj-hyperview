import asyncio
import threading

import pytest
from asgiref.sync import iscoroutinefunction
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory

import dj_hyperview
from dj_hyperview.middleware import (
    HYPERVIEW_VERSION_HEADER,
    HyperviewMiddleware,
    HyperviewRequestDetails,
    detect_hyperview_request,
)


def test_detector_exposes_verified_hyperview_version():
    request = RequestFactory().get(
        "/screen",
        headers={"X-Hyperview-Version": "0.110.0"},
    )

    details = detect_hyperview_request(request)

    assert isinstance(details, HyperviewRequestDetails)
    assert bool(details) is True
    assert details.is_hyperview is True
    assert details.version == "0.110.0"


@pytest.mark.parametrize(
    "accept",
    [
        "application/xml, application/vnd.hyperview+xml",
        "application/vnd.hyperview_fragment+xml; q=0.8, text/html",
    ],
)
def test_detector_accepts_explicit_hyperview_media_types(accept):
    request = RequestFactory().get("/screen", headers={"Accept": accept})

    details = detect_hyperview_request(request)

    assert bool(details) is True
    assert details.is_hyperview is True
    assert details.version is None


@pytest.mark.parametrize(
    "accept",
    [None, "*/*", "application/xml", "application/vnd.hyperview+xml; q=0"],
)
def test_detector_rejects_generic_or_unacceptable_requests(accept):
    headers = {"Accept": accept} if accept is not None else {}
    request = RequestFactory().get("/screen", headers=headers)

    details = detect_hyperview_request(request)

    assert bool(details) is False
    assert details.is_hyperview is False
    assert details.version is None


def test_sync_middleware_attaches_marker_and_preserves_response():
    request = RequestFactory().get(
        "/screen",
        headers={"X-Hyperview-Version": "0.110.0"},
    )
    expected = HttpResponse(
        "screen",
        status=206,
        headers={"X-Screen": "partial"},
    )

    def get_response(incoming_request):
        assert incoming_request.hyperview.version == "0.110.0"
        return expected

    middleware = HyperviewMiddleware(get_response)
    response = middleware(request)

    assert middleware.sync_capable is True
    assert middleware.async_capable is True
    assert iscoroutinefunction(middleware) is False
    assert response is expected
    assert response.status_code == 206
    assert response.headers["X-Screen"] == "partial"


def test_async_middleware_is_native_and_matches_sync_contract():
    request = RequestFactory().get(
        "/screen",
        headers={"Accept": "application/vnd.hyperview+xml"},
    )
    expected = HttpResponse(
        "screen",
        status=207,
        headers={"X-Screen": "multi-status"},
    )
    caller_thread = threading.get_ident()

    async def get_response(incoming_request):
        assert threading.get_ident() == caller_thread
        assert bool(incoming_request.hyperview) is True
        assert incoming_request.hyperview.version is None
        return expected

    async def call_middleware():
        middleware = HyperviewMiddleware(get_response)
        assert iscoroutinefunction(middleware) is True
        return await middleware(request)

    response = asyncio.run(call_middleware())

    assert response is expected
    assert response.status_code == 207
    assert response.headers["X-Screen"] == "multi-status"


def test_middleware_does_not_bypass_django_csrf_protection():
    request = RequestFactory().post(
        "/mutate",
        {"value": "changed"},
        headers={"X-Hyperview-Version": "0.110.0"},
    )

    def protected_view(incoming_request):
        return HttpResponse("mutated")

    csrf_middleware = CsrfViewMiddleware(protected_view)

    def django_handler(incoming_request):
        assert bool(incoming_request.hyperview) is True
        return csrf_middleware.process_view(
            incoming_request,
            protected_view,
            (),
            {},
        )

    response = HyperviewMiddleware(django_handler)(request)

    assert response.status_code == 403


def test_request_integration_api_is_exported_from_package_namespace():
    assert dj_hyperview.HYPERVIEW_VERSION_HEADER == HYPERVIEW_VERSION_HEADER
    assert dj_hyperview.HyperviewMiddleware is HyperviewMiddleware
    assert dj_hyperview.HyperviewRequestDetails is HyperviewRequestDetails
    assert dj_hyperview.detect_hyperview_request is detect_hyperview_request
