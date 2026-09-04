"""Django 6.1 compatibility acceptance tests."""

import django
import pytest
from django.test import Client, override_settings
from tests.consumer_project import settings_filesystem as filesystem

from dj_hyperview import HYPERVIEW_MEDIA_TYPE

HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
}


@pytest.mark.skipif(django.VERSION[:2] != (6, 1), reason="requires Django 6.1")
@override_settings(**HTTP_SETTINGS)
def test_django_61_renders_the_public_consumer_contract(client: Client) -> None:
    """Django 6.1.1 renders escaped HXML through the public response path."""
    response = client.get("/documents/full/", {"title": "6.1 & <supported>"})

    assert django.get_version() == "6.1.1"
    assert response.status_code == 201
    assert response.headers["Content-Type"] == HYPERVIEW_MEDIA_TYPE
    assert response.content.decode() == (
        "<view><header>primary-layout</header>"
        "<text>primary: 6.1 &amp; &lt;supported&gt;</text></view>"
    )
