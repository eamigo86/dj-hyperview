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


def _validate(
    admin_client: Any, content: str, *, name: str = "screens/draft.xml"
) -> dict[str, Any]:
    response = admin_client.post(
        _url(),
        data=json.dumps({"name": name, "content": content}),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response.json()


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_exposes_context_free_validation_control() -> None:
    module, _ = _admin_types()

    widget = module.HyperviewTemplateAdminForm().fields["content"].widget
    rendered = str(widget.render("content", "<view />"))
    media = str(widget.media)

    assert 'data-hyperview-validation-url="/admin/' in rendered
    assert 'class="button djhv-format-validate"' in rendered
    assert "Format and Validate" in rendered
    assert rendered.count('<button type="button"') == 1
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
def test_validation_rejects_static_attribute_missing_from_xsd_catalog(
    admin_client,
) -> None:
    """Static schema lint rejects attributes the selected XSD does not declare."""
    _, model = _admin_types()
    source = (
        '<styles xmlns="https://hyperview.org/hyperview">\n'
        '  <style id="card" kk="1" />\n'
        "</styles>"
    )

    result = _validate(admin_client, source)

    assert result == {
        "ok": False,
        "diagnostics": [
            {
                "severity": "error",
                "code": "schema_attribute",
                "message": 'Attribute "kk" is not allowed on element "style".',
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
@pytest.mark.parametrize(
    ("source", "code", "message"),
    [
        (
            '<mystery xmlns="https://hyperview.org/hyperview" />',
            "schema_element",
            'Element "mystery" is not declared by the selected schema.',
        ),
        (
            '<view xmlns="" />',
            "schema_element",
            'Element "view" is not declared by the selected schema.',
        ),
        (
            '<image xmlns="https://hyperview.org/hyperview" />',
            "schema_required_attribute",
            'Required attribute "source" is missing from element "image".',
        ),
        (
            '<view xmlns="https://hyperview.org/hyperview" '
            'scroll-orientation="sideways" />',
            "schema_attribute_value",
            'Attribute "scroll-orientation" on element "view" has a value '
            "not allowed by the selected schema.",
        ),
    ],
)
def test_validation_checks_static_xsd_declarations(
    admin_client, source: str, code: str, message: str
) -> None:
    result = _validate(admin_client, source)
    diagnostic = result["diagnostics"][0]

    assert result["ok"] is False
    assert (diagnostic["code"], diagnostic["message"], diagnostic["line"]) == (
        code,
        message,
        1,
    )
    if code == "schema_attribute_value":
        assert "sideways" not in json.dumps(result)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "value",
    [
        "{{ direction }}",
        "{% if horizontal %}horizontal{% else %}vertical{% endif %}",
    ],
)
def test_validation_defers_dynamic_xsd_values_until_render(
    admin_client, value: str
) -> None:
    source = (
        f'<view xmlns="https://hyperview.org/hyperview" scroll-orientation="{value}" />'
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_keeps_namespaced_attribute_types_distinct(admin_client) -> None:
    source = (
        '<view xmlns="https://hyperview.org/hyperview" '
        'xmlns:alert="https://hyperview.org/hyperview-alert" '
        'style="card" alert:style="destructive" />'
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_accepts_namespaced_attributes_allowed_by_xsd(admin_client) -> None:
    source = (
        '<doc xmlns="https://hyperview.org/hyperview" '
        'xmlns:app="https://example.test/app" app:tracking="enabled">'
        "<screen><body /></screen>"
        "</doc>"
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_static_validation_degrades_unclosed_raw_block_to_warning() -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    source = "{% comment %}\n<style kk=1>"

    assert module._static_schema_diagnostics("screens/draft.xml", source) == [
        {
            "severity": "warning",
            "code": "schema_static_incomplete",
            "message": (
                "Static schema validation could not analyze the complete "
                "dynamic template structure."
            ),
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 2,
            "column": None,
        }
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_lints_static_markup_inside_real_django_source(admin_client) -> None:
    source = "\n".join(
        (
            '{% load i18n %}<?xml version="1.0" encoding="UTF-8"?>',
            '<doc xmlns="https://hyperview.org/hyperview">',
            "  <styles>",
            "    <!-- Static comments are not schema elements. -->",
            "    {% comment %}<broken attr=1>{% endcomment %}",
            '    {% if dark %}<style id="card" kk="1" />{% endif %}',
            "  </styles>",
            '  <screen><body><text>{% translate "Hello" %}</text></body></screen>',
            "</doc>",
        )
    )

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_attribute"
    assert result["diagnostics"][0]["line"] == 6


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("source", "code"),
    [
        (
            '<view xmlns="https://hyperview.org/hyperview" '
            'scroll-orientation="DJHVSTATICDYNAMIC" />',
            "schema_attribute_value",
        ),
        (
            '<style xmlns="https://hyperview.org/hyperview" kk=1 />',
            "xml_syntax",
        ),
    ],
)
def test_validation_handles_literal_markers_and_static_xml_errors(
    admin_client, source: str, code: str
) -> None:
    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == code
    assert result["diagnostics"][0]["line"] == 1


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "content",
    [
        '<image xmlns="https://hyperview.org/hyperview" {{ attributes }} />',
        '<{{ element }} xmlns="https://hyperview.org/hyperview" />',
        '<image xmlns="https://hyperview.org/hyperview" {{ attribute }}="x" />',
    ],
)
def test_validation_warns_when_dynamic_markup_prevents_complete_static_lint(
    admin_client, content: str
) -> None:
    result = _validate(
        admin_client,
        content,
        name="fragments/image.xml",
    )

    assert result["ok"] is True
    assert result["diagnostics"] == [
        {
            "severity": "warning",
            "code": "schema_static_incomplete",
            "message": (
                "Static schema validation could not analyze the complete "
                "dynamic template structure."
            ),
            "template": "fragments/image.xml",
            "coordinate_space": "source",
            "line": 1,
            "column": None,
        }
    ]


@pytest.mark.django_db
def test_validation_uses_configured_custom_schema_catalog(
    admin_client, tmp_path
) -> None:
    schema = tmp_path / "components.xsd"
    schema.write_text(
        """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
targetNamespace="https://example.test/app" elementFormDefault="qualified">
  <xs:element name="swipe-row">
    <xs:complexType>
      <xs:attribute name="threshold" use="required">
        <xs:simpleType><xs:restriction base="xs:string">
          <xs:enumeration value="short"/><xs:enumeration value="long"/>
        </xs:restriction></xs:simpleType>
      </xs:attribute>
    </xs:complexType>
  </xs:element>
</xs:schema>""",
        encoding="utf-8",
    )
    invalid_value = (
        '<view xmlns="https://hyperview.org/hyperview" '
        'xmlns:app="https://example.test/app">'
        '<app:swipe-row threshold="medium" />'
        "</view>"
    )
    unknown_element = invalid_value.replace(
        '<app:swipe-row threshold="medium" />', "<app:unknown />"
    )

    with override_settings(
        HYPERVIEW={"ADMIN": {"EDITOR": True}, "EXTRA_SCHEMAS": [schema]}
    ):
        value_result = _validate(admin_client, invalid_value)
        element_result = _validate(admin_client, unknown_element)

    assert value_result["ok"] is False
    assert value_result["diagnostics"][0]["code"] == "schema_attribute_value"
    assert element_result["ok"] is False
    assert element_result["diagnostics"][0]["code"] == "schema_element"


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_schema_catalog_failures_safely(
    admin_client, monkeypatch
) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    from dj_hyperview.exceptions import TemplateValidationError

    def fail_catalog():
        raise TemplateValidationError("schema_invalid", "private schema path")

    monkeypatch.setattr(module, "_get_static_validation_catalog", fail_catalog)

    result = _validate(admin_client, "<view />")

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_invalid"
    assert "private schema path" not in json.dumps(result)


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
