"""HTTP acceptance tests for Hyperview middleware and Django CSRF."""

import asyncio
import threading
from xml.etree import ElementTree

import pytest
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings

from dj_hyperview import (
    HYPERVIEW_MEDIA_TYPE,
    HyperviewMiddleware,
    HyperviewResponse,
)
from tests.consumer_project import settings_filesystem as filesystem

HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
    "MIDDLEWARE": filesystem.MIDDLEWARE,
}


@pytest.mark.parametrize(
    ("headers", "version"),
    [
        ({"X-Hyperview-Version": "0.110.0"}, "0.110.0"),
        ({"Accept": HYPERVIEW_MEDIA_TYPE}, ""),
        ({"Accept": "application/vnd.hyperview_fragment+xml; q=0.8"}, ""),
    ],
)
@override_settings(**HTTP_SETTINGS)
def test_header_and_accept_mark_hyperview_request_downstream(
    client: Client, headers: dict[str, str], version: str
) -> None:
    """Verified Hyperview signals expose an equivalent downstream marker."""
    response = client.get("/documents/form/", headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Consumer-Hyperview"] == "true"
    assert response.headers["X-Consumer-Hyperview-Version"] == version


@pytest.mark.parametrize(
    "headers",
    [{}, {"Accept": "application/xml"}, {"Accept": f"{HYPERVIEW_MEDIA_TYPE}; q=0"}],
)
@override_settings(**HTTP_SETTINGS)
def test_absent_or_unacceptable_signals_do_not_mark_request(
    client: Client, headers: dict[str, str]
) -> None:
    """Generic and explicitly rejected media types leave the marker false."""
    response = client.get("/documents/form/", headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Consumer-Hyperview"] == "false"
    assert response.headers["X-Consumer-Hyperview-Version"] == ""


@pytest.mark.parametrize("token", [None, "invalid-token"])
@override_settings(**HTTP_SETTINGS)
def test_form_post_without_valid_csrf_token_is_rejected(token: str | None) -> None:
    """Django rejects missing and invalid package-mode CSRF tokens."""
    client = Client(enforce_csrf_checks=True)
    client.get(
        "/documents/form/",
        headers={"X-Hyperview-Version": "0.110.0"},
    )
    data = {"message": "unsafe mutation"}
    if token is not None:
        data["csrfmiddlewaretoken"] = token

    response = client.post("/documents/form/", data)

    assert response.status_code == 403
    assert b"unsafe mutation" not in response.content


@override_settings(**HTTP_SETTINGS)
def test_form_tag_supplies_real_token_and_valid_post_is_escaped() -> None:
    """The public tag supplies Django's token for an accepted mutation."""
    client = Client(enforce_csrf_checks=True)
    headers = {"Accept": HYPERVIEW_MEDIA_TYPE}
    form = client.get("/documents/form/", headers=headers)
    element = ElementTree.fromstring(form.content)
    field = element.find(
        ".//{https://hyperview.org/hyperview}text-field[@name='csrfmiddlewaretoken']"
    )

    assert form.status_code == 200
    assert field is not None
    assert field.attrib["hide"] == "true"
    assert field.attrib["value"].isalnum()
    assert "csrftoken" in client.cookies

    response = client.post(
        "/documents/form/",
        {
            "message": "Café & <confirmed>",
            "csrfmiddlewaretoken": field.attrib["value"],
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.headers["Content-Type"] == (
        f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"
    )
    assert response.headers["X-Consumer-Hyperview"] == "true"
    confirmed = ElementTree.fromstring(response.content)
    assert (
        confirmed.findtext("{https://hyperview.org/hyperview}text")
        == "accepted: Café & <confirmed>"
    )
    assert b"&amp; &lt;confirmed&gt;" in response.content


def test_sync_and_async_middleware_are_equivalent_and_single_call() -> None:
    """Public sync and async middleware attach one equivalent marker per call."""
    calls: list[tuple[str, bool, str | None]] = []
    caller_thread = threading.get_ident()

    def _sync_response(request: HttpRequest) -> HyperviewResponse:
        details = request.hyperview
        calls.append(("sync", bool(details), details.version))
        return HyperviewResponse(
            "<view xmlns='https://hyperview.org/hyperview'><text>sync</text></view>",
            status=202,
        )

    async def _async_response(request: HttpRequest) -> HyperviewResponse:
        assert threading.get_ident() == caller_thread
        details = request.hyperview
        calls.append(("async", bool(details), details.version))
        return HyperviewResponse(
            "<view xmlns='https://hyperview.org/hyperview'><text>async</text></view>",
            status=202,
        )

    factory = RequestFactory()
    sync_result = HyperviewMiddleware(_sync_response)(
        factory.get("/", headers={"X-Hyperview-Version": "0.110.0"})
    )
    async_result = asyncio.run(
        HyperviewMiddleware(_async_response)(
            factory.get("/", headers={"X-Hyperview-Version": "0.110.0"})
        )
    )

    assert calls == [
        ("sync", True, "0.110.0"),
        ("async", True, "0.110.0"),
    ]
    assert sync_result.status_code == async_result.status_code == 202
    assert (
        sync_result.content
        == b"<view xmlns='https://hyperview.org/hyperview'><text>sync</text></view>"
    )
    assert (
        async_result.content
        == b"<view xmlns='https://hyperview.org/hyperview'><text>async</text></view>"
    )
