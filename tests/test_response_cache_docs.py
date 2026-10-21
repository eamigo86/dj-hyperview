"""Execute the documented public, token-free response-cache example."""

import re
from pathlib import Path
from types import ModuleType

from django.core.cache import caches
from django.test import Client, override_settings
from django.urls import path

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs/http-responses.md"


def test_public_response_cache_example_is_safe_and_executable(tmp_path):
    page = GUIDE.read_text()
    section = page.split("## Cache public responses with Django", 1)[1].split(
        "\n## ", 1
    )[0]
    (source,) = re.findall(r"```python\n(.*?)```", section, re.DOTALL)

    template = tmp_path / "screens/public-home.xml"
    template.parent.mkdir()
    template.write_text(
        '<doc xmlns="https://hyperview.org/hyperview">'
        '<screen id="public"><body /></screen></doc>'
    )
    urls = ModuleType("public_cache_example_urls")
    exec(compile(source, str(GUIDE), "exec"), urls.__dict__)
    urls.urlpatterns = [path("public/", urls.public_home)]
    hxml_calls = []
    html_calls = []
    hxml_constructor = urls.HyperviewTemplateResponse
    html_constructor = urls.HttpResponse

    def counted_hxml(*args, **kwargs):
        hxml_calls.append(1)
        return hxml_constructor(*args, **kwargs)

    def counted_html(*args, **kwargs):
        html_calls.append(1)
        return html_constructor(*args, **kwargs)

    urls.HyperviewTemplateResponse = counted_hxml
    urls.HttpResponse = counted_html
    with override_settings(
        ROOT_URLCONF=urls,
        ALLOWED_HOSTS=["testserver"],
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "public-response-guide",
            }
        },
        HYPERVIEW={
            "TEMPLATE_DIRS": [tmp_path],
            "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        },
    ):
        cache = caches["default"]
        cache.clear()
        client = Client()
        hxml_headers = {"HTTP_ACCEPT": "application/vnd.hyperview+xml"}
        html_headers = {"HTTP_ACCEPT": "text/html"}
        hxml = client.get("/public/", **hxml_headers)
        cached_hxml = client.get("/public/", **hxml_headers)
        assert hxml.content == cached_hxml.content
        assert hxml["Content-Type"].startswith("application/vnd.hyperview+xml")
        assert hxml_calls == [1]
        assert hxml.is_rendered and cached_hxml.is_rendered

        html = client.get("/public/", **html_headers)
        cached_html = client.get("/public/", **html_headers)
        assert html.content == cached_html.content == b"Public home"
        assert html["Content-Type"].startswith("text/plain")
        assert html_calls == [1]
        assert hxml.content != html.content
        for response in (hxml, cached_hxml, html, cached_html):
            assert {part.strip().lower() for part in response["Vary"].split(",")} == {
                "accept",
                "x-hyperview-version",
            }

        versioned = client.get(
            "/public/", HTTP_ACCEPT="text/html", HTTP_X_HYPERVIEW_VERSION="1.0"
        )
        cached_versioned = client.get(
            "/public/", HTTP_ACCEPT="text/html", HTTP_X_HYPERVIEW_VERSION="1.0"
        )
        assert versioned.content == cached_versioned.content == hxml.content
        assert hxml_calls == [1, 1]
        assert html_calls == [1]

        cache.clear()
        client.get("/public/", **hxml_headers)
        assert hxml_calls == [1, 1, 1]
