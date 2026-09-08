"""Tests for context-free validation of unsaved Admin HXML drafts."""

import importlib
import json
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.skipif(
    not apps.is_installed("django.contrib.admin"),
    reason="optional admin settings are not active",
)


def _admin_types() -> tuple[ModuleType, type[Any]]:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    return module, HyperviewTemplate


def _url() -> str:
    return reverse("admin:dj_hyperview_database_hyperviewtemplate_hxml_validate")


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_exposes_context_free_validation_control() -> None:
    module, _ = _admin_types()

    widget = module.HyperviewTemplateAdminForm().fields["content"].widget
    rendered = str(widget.render("content", "<view />"))
    media = str(widget.media)

    assert 'data-hyperview-validation-url="/admin/' in rendered
    assert 'class="button djhv-validate-source"' in rendered
    assert "Validate source" in rendered
    assert "dj_hyperview/admin/hxml_validation.js" in media
    assert "preview" not in rendered.casefold()
    assert "preview" not in media.casefold()


@pytest.mark.django_db
@override_settings(
    HYPERVIEW={
        "ADMIN": {"EDITOR": True},
        "VALIDATION": {"SCHEMA": lambda document: False},
    }
)
def test_validation_accepts_compilable_source_without_context_or_schema_render(
    admin_client,
) -> None:
    """Source validation must not render variables or invoke the HXML schema."""
    _, model = _admin_types()
    source = (
        '<doc xmlns="https://hyperview.org/hyperview">'
        "<styles/><screen><body><text>{{ missing }}</text></body></screen></doc>"
    )

    response = admin_client.post(
        _url(),
        data=json.dumps({"name": "screens/draft.xml", "content": source}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "diagnostics": []}
    assert model.objects.count() == 0


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_django_syntax_at_source_line_without_writing(
    admin_client,
) -> None:
    _, model = _admin_types()

    response = admin_client.post(
        _url(),
        data=json.dumps(
            {
                "name": "screens/draft.xml",
                "content": "<view>\n{% if ready %}<text />\n</view>",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "diagnostics": [
            {
                "severity": "error",
                "code": "django_syntax",
                "message": "Invalid Django template syntax.",
                "template": "screens/draft.xml",
                "coordinate_space": "source",
                "line": 2,
                "column": None,
            }
        ],
    }
    assert model.objects.count() == 0


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_rejects_an_empty_source_without_writing(admin_client) -> None:
    _, model = _admin_types()

    response = admin_client.post(
        _url(),
        data=json.dumps({"name": "screens/draft.xml", "content": ""}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["diagnostics"][0]["code"] == "empty_source"
    assert model.objects.count() == 0


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("../private.xml", "<view />", "invalid_name"),
        (
            "screens/draft.xml",
            "<!DOCTYPE view SYSTEM 'https://invalid.test/x'><view />",
            "forbidden_declaration",
        ),
    ],
)
def test_validation_returns_safe_source_diagnostics(
    admin_client, name: str, content: str, code: str
) -> None:
    response = admin_client.post(
        _url(),
        data=json.dumps({"name": name, "content": content}),
        content_type="application/json",
    )

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == code
    assert content not in response.content.decode()
    if code == "invalid_name":
        assert name not in response.content.decode()


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_transport_and_permissions_fail_closed(
    client, admin_client, django_user_model
) -> None:
    url = _url()
    payload = json.dumps({"name": "screen.xml", "content": "<view />"})

    anonymous = client.post(url, data=payload, content_type="application/json")
    staff = django_user_model.objects.create_user(
        username="draft-reader", password="secret", is_staff=True
    )
    client.force_login(staff)
    forbidden = client.post(url, data=payload, content_type="application/json")
    wrong_method = admin_client.get(url)
    wrong_shape = admin_client.post(
        url,
        data=json.dumps({"name": "screen.xml", "content": "<view />", "extra": 1}),
        content_type="application/json",
    )

    assert anonymous.status_code == 302
    assert forbidden.status_code == 403
    assert wrong_method.status_code == 405
    assert wrong_method.headers["Allow"] == "POST"
    assert wrong_shape.status_code == 400
    assert wrong_shape.json()["diagnostics"][0]["code"] == "invalid_payload"


@pytest.mark.django_db
@override_settings(
    HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_BYTES": 10}}
)
def test_validation_transport_enforces_source_size_and_exact_json(admin_client) -> None:
    url = _url()

    oversized = admin_client.post(
        url,
        data=json.dumps({"name": "screen.xml", "content": "<view>secret</view>"}),
        content_type="application/json",
    )
    duplicate = admin_client.post(
        url,
        data='{"name":"one.xml","name":"two.xml","content":"<view />"}',
        content_type="application/json",
    )
    wrong_type = admin_client.post(url, data="name=x", content_type="text/plain")

    assert oversized.status_code == 413
    assert oversized.json()["diagnostics"][0]["code"] == "input_too_large"
    assert "secret" not in oversized.content.decode()
    assert duplicate.status_code == 400
    assert duplicate.json()["diagnostics"][0]["code"] == "invalid_payload"
    assert wrong_type.status_code == 400
    assert wrong_type.json()["diagnostics"][0]["code"] == "invalid_payload"


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_post_requires_csrf(admin_user) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)

    response = client.post(
        _url(),
        data=json.dumps({"name": "screen.xml", "content": "<view />"}),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_validation_endpoint_is_hidden_when_editor_is_disabled(admin_client) -> None:
    response = admin_client.post(
        _url(),
        data=json.dumps({"name": "screen.xml", "content": "<view />"}),
        content_type="application/json",
    )

    assert response.status_code == 404
