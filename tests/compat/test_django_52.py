"""Django 5.2 compatibility acceptance tests."""

import tomllib
from pathlib import Path

import django
import pytest
from django.test import Client, override_settings
from tests.consumer_project import settings_filesystem as filesystem

from dj_hyperview import HYPERVIEW_MEDIA_TYPE

ROOT = Path(__file__).parents[2]
HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
}


def test_pytest_treats_deprecations_as_compatibility_failures() -> None:
    """The test policy turns Python and Django deprecations into failures."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert metadata["tool"]["pytest"]["ini_options"]["filterwarnings"] == [
        "error::DeprecationWarning",
        "error::PendingDeprecationWarning",
    ]


@pytest.mark.skipif(django.VERSION[:2] != (5, 2), reason="requires Django 5.2")
@override_settings(**HTTP_SETTINGS)
def test_django_52_renders_the_public_consumer_contract(client: Client) -> None:
    """Django 5.2.17 renders escaped HXML through the public response path."""
    response = client.get("/documents/full/", {"title": "5.2 & <supported>"})

    assert django.get_version() == "5.2.17"
    assert response.status_code == 201
    assert response.headers["Content-Type"] == (
        f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"
    )
    assert response.content.decode() == (
        "<view><header>primary-layout</header>"
        "<text>primary: 5.2 &amp; &lt;supported&gt;</text></view>"
    )
