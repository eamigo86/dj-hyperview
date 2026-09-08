"""Admin source checks and completions follow the selected r2 registry."""

import pytest
from django.apps import apps
from django.test import override_settings
from django.urls import reverse

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from tests.test_database_admin_validation import _admin_types, _validate

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not apps.is_installed("django.contrib.admin"),
        reason="optional admin settings are not active",
    ),
]

HV = "https://hyperview.org/hyperview"
ALERT = "https://hyperview.org/hyperview-alert"
EXTENSIONS = {
    "BEHAVIORS": {
        "show-toast": {
            "ATTRIBUTES": {
                "message": {"TYPE": "string", "REQUIRED": True},
                "duration": {"TYPE": "string", "ENUM": ["short", "long"]},
                "count": {"TYPE": "integer"},
                "enabled": {"TYPE": "boolean"},
                "amount": {"TYPE": "decimal"},
            }
        },
        "store-token": {"ATTRIBUTES": {"token": {"TYPE": "string"}}},
    },
    "ELEMENT_ATTRIBUTES": {
        "image": {"variant": {"TYPE": "string", "ENUM": ["face", "fingerprint"]}}
    },
}
CONFIG = {
    "ADMIN": {"EDITOR": True},
    "SCHEMA_EXTENSIONS": EXTENSIONS,
}


@pytest.mark.parametrize(
    "markup",
    [
        '<behavior action="show-toast" message="Hello" duration="short" '
        'count="2" enabled="true" amount="1.5" />',
        '<behavior action="show-toast" message="Hello" '
        f'xmlns:a="{ALERT}" a:message="Alert body"/>',
        '<behavior action="store-token" token="abc"/>',
        '<behavior action="push" href="/next"/>',
        '<image source="/face.png" style="avatar" variant="face"/>',
        '<text ellipsizeMode="tail" accessibilityRole="button"/>',
        '<style width="12.5%" margin="-12.5%"/>',
    ],
)
@override_settings(HYPERVIEW=CONFIG)
def test_r2_static_and_rendered_validation_accept_the_same_literal_types(
    admin_client, markup
):
    source = markup.replace(" ", f' xmlns="{HV}" ', 1)
    validate_hyperview_schema(source)
    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.parametrize(
    ("markup", "code"),
    [
        ('<behavior action="show-toast"/>', "schema_required_attribute"),
        (
            '<behavior action="show-toast" message="Hello" duration="medium"/>',
            "schema_attribute_value",
        ),
        (
            '<behavior action="show-toast" message="Hello" count="1.5"/>',
            "schema_attribute_value",
        ),
        (
            '<behavior action="show-toast" message="Hello" enabled="yes"/>',
            "schema_attribute_value",
        ),
        (
            '<behavior action="show-toast" message="Hello" amount="NaN"/>',
            "schema_attribute_value",
        ),
        (
            '<behavior action="show-toast" message="Hello" token="abc"/>',
            "schema_attribute",
        ),
        ('<behavior action="store-token" message="Hello"/>', "schema_attribute"),
        ('<behavior action="push" message="Hello"/>', "schema_attribute"),
        ('<behavior action="typo"/>', "schema_attribute_value"),
        ('<view action="show-toast"/>', "schema_attribute_value"),
        ('<text ellipsizeMode="sideways"/>', "schema_attribute_value"),
        ('<image source="/x" style="x" variant="unknown"/>', "schema_attribute_value"),
        ('<text variant="face"/>', "schema_attribute"),
        ('<style width="bananas"/>', "schema_attribute_value"),
        ('<style width="-1%"/>', "schema_attribute_value"),
        ('<style fontSize="0"/>', "schema_attribute_value"),
    ],
)
@override_settings(HYPERVIEW=CONFIG)
def test_r2_static_and_rendered_validation_reject_the_same_literal_types(
    admin_client, markup, code
):
    source = markup.replace(" ", f' xmlns="{HV}" ', 1)
    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(source)
    result = _validate(admin_client, source)
    assert result["ok"] is False
    assert any(
        item["code"] == code and item["line"] == 1 for item in result["diagnostics"]
    )


@pytest.mark.parametrize(
    "source",
    [
        '<behavior action="{{ action }}" message="Hello"/>',
        '<behavior action="{% if ready %}show-toast{% else %}push{% endif %}" '
        'message="Hello"/>',
        '<behavior action="show-toast" message="Hello" count="{{ count }}"/>',
        '{% if ready %}<behavior action="show-toast" message="Hello"/>{% else %}'
        '<behavior action="store-token" token="abc"/>{% endif %}',
        "<text>{{ label }}</text>",
        '{% include "fragments/message.xml" %}',
        '{% verbatim %}<behavior action="{{ literal }}"/>{% endverbatim %}',
    ],
)
@override_settings(HYPERVIEW=CONFIG)
def test_r2_dynamic_registry_source_warns_instead_of_claiming_full_validation(
    admin_client, source
):
    result = _validate(admin_client, source)
    assert result["ok"] is True
    assert result["diagnostics"]
    assert all(item["severity"] == "warning" for item in result["diagnostics"])
    assert result["diagnostics"][-1]["code"] == "schema_static_incomplete"


@override_settings(HYPERVIEW=CONFIG)
def test_r2_dynamic_action_still_checks_common_literal_types(admin_client):
    result = _validate(
        admin_client, '<behavior action="{{ action }}" once="yes" message="Hello"/>'
    )
    assert result["ok"] is False
    assert [item["code"] for item in result["diagnostics"]] == [
        "schema_attribute_value",
        "schema_static_incomplete",
    ]


@override_settings(HYPERVIEW=CONFIG)
def test_r2_alert_message_cannot_satisfy_custom_unqualified_required_message(
    admin_client,
):
    source = f'<behavior action="show-toast" xmlns:a="{ALERT}" a:message="Alert"/>'
    result = _validate(admin_client, source)
    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_required_attribute"


@pytest.mark.parametrize("submit_action", ["_save", "_addanother", "_continue"])
@override_settings(HYPERVIEW=CONFIG)
def test_r2_extended_template_saves_without_rewriting_or_browser_dependency(
    admin_client, submit_action
):
    _, model = _admin_types()
    source = (
        '<view>\n  <behavior action="show-toast" message="  Hello  " '
        'duration="short"/>\n</view>'
    )
    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}
    response = admin_client.post(
        reverse("admin:dj_hyperview_database_hyperviewtemplate_add"),
        {
            "name": "screens/extension.xml",
            "content": source,
            "active": "on",
            submit_action: "Save",
        },
    )
    assert response.status_code == 302
    assert model.objects.get(name="screens/extension.xml").content == source


@override_settings(HYPERVIEW=CONFIG)
def test_r2_server_save_rejects_wrong_action_attributes_on_source_line(admin_client):
    _, model = _admin_types()
    source = '<view>\n  <behavior action="push" message="not built in"/>\n</view>'
    response = admin_client.post(
        reverse("admin:dj_hyperview_database_hyperviewtemplate_add"),
        {
            "name": "screens/extension.xml",
            "content": source,
            "active": "on",
            "_save": "Save",
        },
    )
    assert response.status_code == 200
    assert model.objects.count() == 0
    error = response.context["adminform"].form.errors.as_data()["content"][0]
    assert error.code == "schema_attribute"
    assert 'Attribute "message"' in str(error.message)
    assert _validate(admin_client, source)["diagnostics"][0]["line"] == 2
