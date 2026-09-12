"""Execute the Quick Start's screen-to-fragment HTTP round trip."""

import re
from pathlib import Path
from types import ModuleType

import pytest
from django.test import Client, override_settings
from lxml import etree

from dj_hyperview import TemplateValidationError

ROOT = Path(__file__).parents[1]
NS = {"hv": "https://hyperview.org/hyperview"}


def test_request_lifecycle_is_discoverable():
    page = (ROOT / "docs/quickstart.md").read_text()
    assert "## Request lifecycle" in page
    for guide in ("index.md", "mobile-getting-started.md", "http-responses.md"):
        assert "quickstart.md#request-lifecycle" in (ROOT / "docs" / guide).read_text()


def test_documented_screen_and_tap_return_validated_hxml(tmp_path):
    page = (ROOT / "docs/quickstart.md").read_text()
    assert "## 5. Follow a tap back to Django" in page
    example = page.split("## 5. Follow a tap back to Django", 1)[1]
    screen, fragment = re.findall(r"```xml\n(.*?)```", example, re.DOTALL)
    (python,) = re.findall(r"```python\n(.*?)```", example, re.DOTALL)
    for name, source in (
        ("screens/home.xml", screen),
        ("fragments/greeting.xml", fragment),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    urls = ModuleType("lifecycle_example_urls")
    exec(compile(python, "docs/quickstart.md", "exec"), urls.__dict__)
    with override_settings(
        ROOT_URLCONF=urls,
        ALLOWED_HOSTS=["testserver"],
        HYPERVIEW={
            "TEMPLATE_DIRS": [tmp_path],
            "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        },
    ):
        client = Client()
        response = client.get("/hyperview/home/")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith(
            "application/vnd.hyperview+xml"
        )
        document = etree.fromstring(response.content)
        (button,) = document.xpath("//hv:text[@href]", namespaces=NS)
        assert button.get("action") == "replace"
        assert button.get("verb") == "get"
        target = button.get("target")
        assert document.xpath("//*[@id=$target]", target=target)
        update = client.get(button.get("href"))
        assert update.status_code == 200
        assert update.headers["Content-Type"].startswith(
            "application/vnd.hyperview_fragment+xml"
        )
        element = etree.fromstring(update.content)
        assert element.tag == f"{{{NS['hv']}}}view"
        assert element.get("id") == target
        assert "Server time:" in "".join(element.itertext())
        assert not update.content.startswith(b"<doc")
        # The same endpoint must reject invalid output, not serve it to the phone.
        (tmp_path / "fragments/greeting.xml").write_text(
            '<view xmlns="https://hyperview.org/hyperview"><unknown /></view>'
        )
        with pytest.raises(TemplateValidationError):
            client.get(button.get("href"))
