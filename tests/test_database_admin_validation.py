"""Tests for context-free validation of unsaved Admin HXML drafts."""

import gc
import importlib
import json
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Never

import pytest
from django.apps import apps
from django.test import Client, override_settings
from django.urls import reverse

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.validation import validate_hxml
from tests.test_database_template_syntax import NESTED_BLOCKS, nested
from tests.test_validation_hardening import (
    HYPERVIEW_XMLNS,
    prefixed_namespaces,
    rebinding_path,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

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
def test_static_validation_uses_collision_safe_dynamic_markers() -> None:
    """Static validation picks dynamic markers that cannot collide with the source."""
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    source = (
        f"{module._DYNAMIC_VALUE} {module._DYNAMIC_STRUCTURE} "
        '<text xmlns="https://hyperview.org/hyperview">{{ label }}</text>'
    )

    masked, value_marker, structure_marker, dynamic = module._mask_template_syntax(
        source
    )

    assert value_marker not in {module._DYNAMIC_VALUE, module._DYNAMIC_STRUCTURE}
    assert structure_marker not in {module._DYNAMIC_VALUE, module._DYNAMIC_STRUCTURE}
    assert value_marker in masked
    assert dynamic is False


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_exposes_context_free_validation_control() -> None:
    """The enabled editor exposes a validation control that needs no context."""
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
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_accepts_compilable_source_without_context_or_schema_render(
    admin_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source validation must not render variables or invoke the HXML schema.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Makes any schema validation of rendered output fail the test.
    """
    monkeypatch.setattr(
        "dj_hyperview.engine._validate_hxml_result",
        lambda *args, **kwargs: pytest.fail("Admin rendered template source"),
    )
    _, model = _admin_types()
    source = (
        '<doc xmlns="https://hyperview.org/hyperview">'
        "<screen><styles/><body><text>{{ missing }}</text></body></screen></doc>"
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
    admin_client: Client,
) -> None:
    """Static schema lint rejects attributes the selected XSD does not declare.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
    """
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
@pytest.mark.parametrize("submit_action", ["_save", "_addanother", "_continue"])
def test_admin_save_actions_reject_invalid_static_schema_without_javascript(
    admin_client: Client, submit_action: str
) -> None:
    """The server form remains authoritative when browser checks are bypassed.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        submit_action: Name of the admin submit button that is clicked.
    """
    _, model = _admin_types()
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")
    source = (
        '<styles xmlns="https://hyperview.org/hyperview">\n'
        '  <style id="card" kk="1" />\n'
        "</styles>"
    )

    response = admin_client.post(
        add,
        {
            "name": "screens/draft.xml",
            "content": source,
            "active": "on",
            submit_action: "Save",
        },
    )

    errors = response.context["adminform"].form.errors.as_data()
    assert response.status_code == 200
    assert model.objects.count() == 0
    assert errors["content"][0].code == "schema_attribute"
    assert 'Attribute "kk" is not allowed on element "style".' in str(
        errors["content"][0].message
    )
    assert source not in str(errors)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_admin_save_allows_informational_static_schema_result() -> None:
    """Incomplete static analysis informs in the browser but does not block save."""
    module, _ = _admin_types()
    source = '<image xmlns="https://hyperview.org/hyperview" {{ attributes }} />'
    form = module.HyperviewTemplateAdminForm(
        data={
            "name": "screens/dynamic.xml",
            "content": source,
            "active": True,
        }
    )

    assert form.is_valid(), form.errors.as_data()


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_admin_form_skips_draft_validation_when_required_fields_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Field errors remain authoritative and never pass partial values onward.

    Args:
        monkeypatch: Makes any draft validation fail the test.
    """
    module, _ = _admin_types()

    def unexpected_validation(name: str, content: str) -> dict[str, Any]:
        raise AssertionError((name, content))

    monkeypatch.setattr(module, "validate_draft_source", unexpected_validation)
    form = module.HyperviewTemplateAdminForm(data={"active": True})

    assert form.is_valid() is False
    assert {"name", "content"} <= set(form.errors)


@pytest.mark.django_db
@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_admin_form_maps_invalid_draft_name_to_name_field() -> None:
    """The admin form maps an invalid draft name to the name field."""
    module, _ = _admin_types()
    form = module.HyperviewTemplateAdminForm(
        data={"name": "../private.xml", "content": "<view />", "active": True}
    )

    assert form.is_valid() is False
    assert form.errors.as_data()["name"][0].code == "invalid_name"


@pytest.mark.django_db
@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_admin_form_fails_closed_when_draft_validation_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin form fails closed, without details, when draft validation is
    unavailable.

    Args:
        monkeypatch: Makes draft validation raise a configuration error.
    """
    module, _ = _admin_types()
    from dj_hyperview.exceptions import HyperviewConfigurationError

    def unavailable(name: str, content: str) -> dict[str, Any]:
        raise HyperviewConfigurationError(["unsafe internal detail"])

    monkeypatch.setattr(module, "validate_draft_source", unavailable)
    form = module.HyperviewTemplateAdminForm(
        data={"name": "screen.xml", "content": "<view />", "active": True}
    )

    assert form.is_valid() is False
    error = form.non_field_errors().as_data()[0]
    assert error.code == "configuration_error"
    assert str(error.message) == "Template validation is unavailable."
    assert "unsafe internal detail" not in str(form.errors.as_data())


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
    admin_client: Client, source: str, code: str, message: str
) -> None:
    """Validation checks the static declarations against the selected XSD.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        source: Draft that breaks one XSD declaration.
        code: Diagnostic code the validation is expected to report.
        message: Diagnostic message the validation is expected to report.
    """
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
        "literal > {% if horizontal %}horizontal{% else %}vertical{% endif %}",
    ],
)
def test_validation_defers_dynamic_xsd_values_until_render(
    admin_client: Client, value: str
) -> None:
    """Validation defers dynamic attribute values to render time.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        value: Dynamic value placed in the scroll-orientation attribute.
    """
    source = (
        f'<view xmlns="https://hyperview.org/hyperview" scroll-orientation="{value}" />'
    )

    result = _validate(admin_client, source)
    assert result == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_keeps_namespaced_attribute_types_distinct(
    admin_client: Client,
) -> None:
    """Validation keeps the types of namespaced attributes apart from unqualified ones.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<view xmlns="https://hyperview.org/hyperview" '
        'xmlns:alert="https://hyperview.org/hyperview-alert" '
        'style="card" alert:style="destructive" />'
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_accepts_namespaced_attributes_allowed_by_xsd(
    admin_client: Client,
) -> None:
    """Validation accepts the namespaced attributes that the XSD allows.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<doc xmlns="https://hyperview.org/hyperview" '
        'xmlns:app="https://example.test/app" app:tracking="enabled">'
        "<screen><body /></screen>"
        "</doc>"
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_static_validation_defers_unclosed_raw_block_analysis() -> None:
    """Static validation defers the analysis of an unclosed raw block."""
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    source = "{% comment %}\n<style kk=1>"

    assert module._static_schema_diagnostics("screens/draft.xml", source) == [
        {
            "severity": "info",
            "code": "schema_static_incomplete",
            "message": (
                "Static checks passed. Final HXML will be validated when served."
            ),
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 2,
            "column": None,
        }
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_lints_static_markup_inside_real_django_source(
    admin_client: Client,
) -> None:
    """Validation lints the static markup inside real Django template source.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
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
def test_validation_does_not_treat_load_as_an_incomplete_schema_gap(
    admin_client: Client,
) -> None:
    """Validation does not treat a load tag as an incomplete schema check.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    source = "\n".join(
        (
            '{% load i18n %}<?xml version="1.0" encoding="UTF-8"?>',
            '<doc xmlns="https://hyperview.org/hyperview">',
            "  <screen><body /></screen>",
            "</doc>",
        )
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}
    assert module._first_runtime_token_line(source) is None


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_treats_a_dynamic_attribute_value_as_an_unknown_scalar(
    admin_client: Client,
) -> None:
    """Validation treats a dynamic attribute value as an unknown scalar.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = "\n".join(
        (
            '{% load i18n %}<?xml version="1.0" encoding="UTF-8"?>',
            '<view xmlns="https://hyperview.org/hyperview"',
            '  scroll-orientation="{{ direction }}" />',
        )
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_dynamic_scalar_does_not_hide_an_invalid_attribute_name(
    admin_client: Client,
) -> None:
    """A dynamic scalar value does not hide an invalid attribute name.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<style xmlns="https://hyperview.org/hyperview" id="card" '
        'backgroundColor="{{ theme.canvas }}" kk="1" />'
    )

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_attribute"
    assert result["diagnostics"][0]["message"] == (
        'Attribute "kk" is not allowed on element "style".'
    )


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_checks_the_position_of_dynamic_scalar_text(
    admin_client: Client,
) -> None:
    """Validation checks where dynamic scalar text is allowed.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = "\n".join(
        (
            '<screen xmlns="https://hyperview.org/hyperview">',
            "  {{ unexpected_text }}",
            "  <body />",
            "</screen>",
        )
    )

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"] == [
        {
            "severity": "error",
            "code": "schema_structure",
            "message": 'Text content is not allowed inside element "screen".',
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 1,
            "column": None,
        }
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "dynamic_markup",
    [
        "{% include optional_template %}",
        "<{{ element_name }} />",
    ],
)
def test_structural_django_markup_is_deferred_without_false_schema_errors(
    admin_client: Client, dynamic_markup: str
) -> None:
    """Structural Django markup is deferred without false schema errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        dynamic_markup: Django template markup that the static check cannot resolve.
    """
    source = (
        '<doc xmlns="https://hyperview.org/hyperview">'
        f"{dynamic_markup}<screen><body /></screen></doc>"
    )

    result = _validate(admin_client, source)

    assert result["ok"] is True
    assert [item["code"] for item in result["diagnostics"]] == [
        "schema_static_incomplete"
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_mutually_exclusive_valid_branches_do_not_create_structure_errors(
    admin_client: Client,
) -> None:
    """Mutually exclusive valid branches do not create structure errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<doc xmlns="https://hyperview.org/hyperview"><screen>'
        "{% if dark %}<styles />{% else %}<styles />{% endif %}"
        "<body /></screen></doc>"
    )

    result = _validate(admin_client, source)

    assert result["ok"] is True
    assert [item["code"] for item in result["diagnostics"]] == [
        "schema_static_incomplete"
    ]


def _filesystem_admin_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "ADMIN": {"EDITOR": True},
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    }


def _count_resolutions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from dj_hyperview.resolver import TemplateResolver

    calls: list[str] = []
    original = TemplateResolver.resolve

    def counting(self: Any, name: str) -> Any:
        calls.append(name)
        return original(self, name)

    monkeypatch.setattr(TemplateResolver, "resolve", counting)
    return calls


@pytest.mark.django_db
def test_validation_expands_a_literal_include_from_configured_sources(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation expands a literal include from the configured sources.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "label.xml").write_text("<text>{{ label }}</text>", encoding="utf-8")
    source = (
        '<view xmlns="https://hyperview.org/hyperview">'
        '{% include "partials/label.xml" %}'
        "</view>"
    )

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
def test_literal_include_with_dynamic_structure_is_deferred_without_false_errors(
    admin_client: Client, tmp_path: Path
) -> None:
    """A literal include with dynamic structure is deferred without false errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "panel.xml").write_text(
        '<view xmlns="https://hyperview.org/hyperview">'
        "{% if error %}<text>{{ error }}</text>{% endif %}"
        "</view>",
        encoding="utf-8",
    )
    source = (
        '<view xmlns="https://hyperview.org/hyperview">'
        "<!-- The owner's dynamic panel. -->"
        '{% include "partials/panel.xml" %}'
        "</view>"
    )

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is True
    assert {item["code"] for item in result["diagnostics"]} == {
        "schema_static_incomplete"
    }


@pytest.mark.django_db
def test_validation_resolves_a_literal_include_relative_to_the_draft_name(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation resolves a literal include relative to the draft name.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "screens" / "partials"
    partials.mkdir(parents=True)
    (partials / "label.xml").write_text("<text>Label</text>", encoding="utf-8")
    source = (
        '<view xmlns="https://hyperview.org/hyperview">'
        '{% include "./partials/label.xml" %}'
        "</view>"
    )

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
def test_validation_reports_a_missing_literal_include(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation reports a literal include that does not exist.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    source = '{% include "partials/missing.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"] == [
        {
            "severity": "error",
            "code": "django_include_missing",
            "message": 'Included template "partials/missing.xml" was not found.',
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 1,
            "column": None,
        }
    ]


@pytest.mark.django_db
def test_validation_rejects_an_unsafe_literal_include_without_echoing_it(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation rejects an unsafe literal include without echoing its name.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    source = '{% include "/private/secret.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "django_include_invalid"
    assert result["diagnostics"][0]["message"] == "Included template name is invalid."
    assert "/private/secret.xml" not in json.dumps(result)


@pytest.mark.django_db
def test_validation_reports_schema_errors_in_a_literal_include(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation reports the schema errors of a literal include.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "invalid.xml").write_text(
        '<style xmlns="https://hyperview.org/hyperview" id="card" kk="1" />',
        encoding="utf-8",
    )
    source = '{% include "partials/invalid.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0] == {
        "severity": "error",
        "code": "schema_attribute",
        "message": 'Attribute "kk" is not allowed on element "style".',
        "template": "partials/invalid.xml",
        "coordinate_space": "source",
        "line": 1,
        "column": None,
    }


@pytest.mark.django_db
def test_markers_never_occur_in_the_source_of_a_draft_or_its_includes(
    admin_client: Client, tmp_path: Path
) -> None:
    """The draft and the include each hold a number, and the composed source both.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        tmp_path: Temporary directory that holds the files of the test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "marked.xml").write_text(
        f'<view xmlns="{HV}" scroll-orientation="DJHVSTATICDYNAMIC0" '
        'id="{{ id }}" />',
        encoding="utf-8",
    )
    source = 'DJHVSTATICDYNAMIC1 {% include "partials/marked.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert ("partials/marked.xml", "schema_attribute_value") in [
        (item["template"], item["code"]) for item in result["diagnostics"]
    ]


@pytest.mark.django_db
def test_validation_reports_django_syntax_in_a_literal_include(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation reports Django syntax errors in a literal include.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "invalid.xml").write_text("{% if ready %}<text />", encoding="utf-8")
    source = '{% include "partials/invalid.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "django_syntax"
    assert result["diagnostics"][0]["template"] == "partials/invalid.xml"
    assert "Unclosed tag" in result["diagnostics"][0]["message"]


@pytest.mark.django_db
def test_validation_does_not_check_an_include_used_inside_an_attribute_value(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation does not expand an include used inside an attribute value.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "bad.xml").write_text(
        '<style xmlns="https://hyperview.org/hyperview" id="card" kk="1" />',
        encoding="utf-8",
    )
    source = f'<text xmlns="{HV}" style="{{% include \'partials/bad.xml\' %}}" />'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
def test_validation_checks_literal_include_content_in_its_parent_position(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation checks the included content in the position of its parent.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "styles.xml").write_text("<styles />", encoding="utf-8")
    source = "\n".join(
        (
            '<doc xmlns="https://hyperview.org/hyperview">',
            "  <screen>",
            "    <body />",
            '    {% include "partials/styles.xml" %}',
            "  </screen>",
            "</doc>",
        )
    )

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_structure"
    assert result["diagnostics"][0]["template"] == "screens/draft.xml"
    assert result["diagnostics"][0]["line"] == 4


@pytest.mark.django_db
def test_validation_rejects_literal_include_cycles(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation rejects cycles between literal includes.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "loop.xml").write_text(
        '{% include "partials/loop.xml" %}', encoding="utf-8"
    )
    source = '{% include "partials/loop.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "django_include_cycle"
    assert result["diagnostics"][0]["template"] == "partials/loop.xml"


@pytest.mark.django_db
def test_validation_limits_literal_include_expansion(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation limits how far literal includes are expanded.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "label.xml").write_text("<text>Label</text>", encoding="utf-8")
    source = "".join('{% include "partials/label.xml" %}' for _ in range(65))

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result == {
        "ok": True,
        "diagnostics": [
            {
                "severity": "info",
                "code": "schema_static_incomplete",
                "message": (
                    "Static checks passed. Final HXML will be validated when served."
                ),
                "template": "screens/draft.xml",
                "coordinate_space": "source",
                "line": 1,
                "column": None,
            }
        ],
    }


@pytest.mark.django_db
def test_include_expansion_stops_at_the_limit_and_keeps_real_errors(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Include expansion stops at the limit and still reports the real errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Counts the template resolutions of the draft and its includes.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "label.xml").write_text("<text>Label</text>", encoding="utf-8")
    calls = _count_resolutions(monkeypatch)
    include = '{% include "partials/label.xml" %}'
    source = f'{include * 70}\n<style id="card" kk="1" />'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert len(calls) == module._MAX_LITERAL_INCLUDES
    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_attribute"
    assert {item["code"] for item in result["diagnostics"][1:]} == {
        "schema_static_incomplete"
    }


@pytest.mark.django_db
def test_validation_deduplicates_findings_from_a_repeated_literal_include(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation reports the findings of a repeated include only once.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "invalid.xml").write_text(
        '<style xmlns="https://hyperview.org/hyperview" id="card" kk="1" />',
        encoding="utf-8",
    )
    source = '{% include "partials/invalid.xml" %}' * 2

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert [item["code"] for item in result["diagnostics"]] == ["schema_attribute"]


@pytest.mark.django_db
def test_include_expansion_limit_covers_nested_includes_with_one_note_per_template(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The expansion limit covers nested includes and adds one note per template.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Counts the template resolutions of the draft and its includes.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "leaf.xml").write_text("<text>Leaf</text>", encoding="utf-8")
    (partials / "inner.xml").write_text(
        '{% include "partials/leaf.xml" %}' * 40, encoding="utf-8"
    )
    calls = _count_resolutions(monkeypatch)
    source = (
        '{% include "partials/inner.xml" %}' * 2 + '{% include "partials/leaf.xml" %}'
    )

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert len(calls) == module._MAX_LITERAL_INCLUDES
    assert result["ok"] is True
    assert sorted(
        (item["template"], item["code"], item["severity"])
        for item in result["diagnostics"]
    ) == [
        ("partials/inner.xml", "schema_static_incomplete", "info"),
        ("screens/draft.xml", "schema_static_incomplete", "info"),
    ]


@pytest.mark.django_db
def test_validation_reports_an_unavailable_literal_include_source_safely(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validation reports an unavailable include source without leaking details.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Makes the template resolver fail for every include.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    from dj_hyperview.exceptions import SourceUnavailable
    from dj_hyperview.resolver import TemplateResolver

    def fail_source(self: Any, name: str) -> Never:
        raise SourceUnavailable("private:source", "private failure")

    monkeypatch.setattr(TemplateResolver, "resolve", fail_source)

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, '{% include "partials/label.xml" %}')

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "django_include_unavailable"
    assert "private" not in json.dumps(result)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_static_structure_validation_ignores_non_element_top_level_nodes(
    admin_client: Client,
) -> None:
    """Static structure validation ignores top-level nodes that are not elements.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<!-- Documentation only. --><view xmlns="https://hyperview.org/hyperview" />'
    )

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_rejects_statically_visible_child_order(
    admin_client: Client,
) -> None:
    """Validation rejects a child order that is visible in the static markup.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = "\n".join(
        (
            '<doc xmlns="https://hyperview.org/hyperview">',
            '  <styles><style id="screen" color="{{ theme.ink }}" /></styles>',
            "  <screen><body /></screen>",
            "</doc>",
        )
    )

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert result["diagnostics"] == [
        {
            "severity": "error",
            "code": "schema_structure",
            "message": (
                'Element "styles" is not allowed inside "doc" at this position; '
                'expected "screen" or "navigator".'
            ),
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 2,
            "column": None,
        }
    ]


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
            # The marker is numbered after the highest one in the source.
            '<view xmlns="https://hyperview.org/hyperview" '
            'scroll-orientation="DJHVSTATICDYNAMIC0" id="{{ id }}" />',
            "schema_attribute_value",
        ),
        (
            '<style xmlns="https://hyperview.org/hyperview" kk=1 />',
            "xml_syntax",
        ),
    ],
)
def test_validation_handles_literal_markers_and_static_xml_errors(
    admin_client: Client, source: str, code: str
) -> None:
    """Validation handles literal markers and static XML errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        source: Draft that holds a literal marker or malformed XML.
        code: Diagnostic code the validation is expected to report.
    """
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
def test_validation_informs_when_dynamic_markup_prevents_complete_static_lint(
    admin_client: Client, content: str
) -> None:
    """Validation adds a note when dynamic markup prevents a complete static lint.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        content: Draft whose dynamic markup hides part of the structure.
    """
    result = _validate(
        admin_client,
        content,
        name="fragments/image.xml",
    )

    assert result["ok"] is True
    assert result["diagnostics"] == [
        {
            "severity": "info",
            "code": "schema_static_incomplete",
            "message": (
                "Static checks passed. Final HXML will be validated when served."
            ),
            "template": "fragments/image.xml",
            "coordinate_space": "source",
            "line": 1,
            "column": None,
        }
    ]


@pytest.mark.django_db
def test_validation_uses_configured_custom_schema_catalog(
    admin_client: Client, tmp_path: Path
) -> None:
    """Validation uses the configured custom schema catalog.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
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
    admin_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation reports schema catalog failures without leaking details.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Makes loading the static schema catalog fail.
    """
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    from dj_hyperview.exceptions import TemplateValidationError

    def fail_catalog() -> Never:
        raise TemplateValidationError("schema_invalid", "private schema path")

    monkeypatch.setattr(module, "_get_static_validation_catalog", fail_catalog)

    result = _validate(admin_client, "<view />")

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "schema_invalid"
    assert "private schema path" not in json.dumps(result)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_django_syntax_at_source_line_without_writing(
    admin_client: Client,
) -> None:
    """Validation reports Django syntax errors at the source line and writes nothing.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
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
                "message": (
                    "Django template syntax error: Unclosed tag on line 2: "
                    "'if'. Looking for one of: elif, else, endif."
                ),
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
def test_validation_explains_invalid_arguments_to_a_django_tag(
    admin_client: Client,
) -> None:
    """Validation explains invalid arguments to a Django tag.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    result = _validate(admin_client, '{% include "partial.xml" banana %}')

    assert result["ok"] is False
    assert result["diagnostics"][0]["code"] == "django_syntax"
    assert result["diagnostics"][0]["message"] == (
        "Django template syntax error: Unknown argument for 'include' tag: 'banana'."
    )


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_django_syntax_diagnostic_redacts_unbounded_parser_messages() -> None:
    """The Django syntax diagnostic redacts parser messages of unbounded length."""
    module = importlib.import_module("dj_hyperview.contrib.database.admin_validation")
    from django.template import TemplateSyntaxError

    assert module._django_syntax_message(TemplateSyntaxError("x" * 501)) == (
        "Invalid Django template syntax."
    )


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_rejects_an_empty_source_without_writing(
    admin_client: Client,
) -> None:
    """Validation rejects an empty source and writes nothing.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
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
    admin_client: Client, name: str, content: str, code: str
) -> None:
    """Validation returns safe diagnostics for invalid names and sources.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        name: Draft name posted to the endpoint.
        content: Draft source posted to the endpoint.
        code: Diagnostic code the endpoint is expected to return.
    """
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
    client: Client, admin_client: Client, django_user_model: "type[AbstractUser]"
) -> None:
    """The validation transport and its permissions fail closed.

    Args:
        client: Client that starts anonymous and is later signed in as other users.
        admin_client: Client signed in as a superuser.
        django_user_model: Django user model class used to create staff users.
    """
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
def test_validation_transport_enforces_source_size_and_exact_json(
    admin_client: Client,
) -> None:
    """The validation transport enforces the source size and an exact JSON body.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
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
def test_validation_post_requires_csrf(admin_user: "AbstractUser") -> None:
    """A validation POST without a CSRF token is rejected.

    Args:
        admin_user: Superuser that the test signs in.
    """
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)

    response = client.post(
        _url(),
        data=json.dumps({"name": "screen.xml", "content": "<view />"}),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_validation_endpoint_is_hidden_when_editor_is_disabled(
    admin_client: Client,
) -> None:
    """The validation endpoint does not exist while the editor is disabled.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    response = admin_client.post(
        _url(),
        data=json.dumps({"name": "screen.xml", "content": "<view />"}),
        content_type="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_conditional_structure_is_informational_without_hiding_errors(
    admin_client: Client,
) -> None:
    """Conditional structure gets a note without hiding real errors.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = (
        '<doc xmlns="https://hyperview.org/hyperview"><screen><body>\n'
        "{% if enabled %}<view><text>Ready</text></view>{% endif %}\n"
        "</body></screen></doc>"
    )
    result = _validate(admin_client, source)
    assert result["ok"] is True
    assert result["diagnostics"] == [
        {
            "severity": "info",
            "code": "schema_static_incomplete",
            "message": (
                "Static checks passed. Final HXML will be validated when served."
            ),
            "template": "screens/draft.xml",
            "coordinate_space": "source",
            "line": 2,
            "column": None,
        }
    ]
    invalid = _validate(admin_client, source.replace("<view>", '<view forbidden="1">'))
    assert invalid["ok"] is False
    assert any(item["severity"] == "error" for item in invalid["diagnostics"])


HV = "https://hyperview.org/hyperview"
NESTED_IF = "{% if x %}" * 3000 + "{% endif %}" * 3000


@pytest.fixture
def finalized_garbage() -> None:
    """Finalize pending garbage here, not in the middle of a deep recursion.

    A finalizer that the collector runs when the stack is nearly full fails with
    RecursionError, and pytest then reports that as the failure of the test.
    """
    gc.collect()


def _render(source: str) -> str:
    """Render with the real Django engine, as a response would."""
    return HyperviewEngine().backend.from_string(source).render({})


def _static_module() -> ModuleType:
    return importlib.import_module("dj_hyperview.contrib.database.admin_validation")


def _forbid(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Fail the test if the static pass reaches the named stage."""
    module = _static_module()

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("the static pass must stop before this stage")

    for name in names:
        monkeypatch.setattr(module, name, unreachable)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "opening",
    [
        '{% comment "temporarily disabled" %}',
        "{% comment 'note' %}",
        "{% comment note %}",
        "{% comment %}",
    ],
)
def test_validation_ignores_commented_out_markup_whatever_the_note(
    admin_client: Client, opening: str
) -> None:
    """Django's comment tag ignores everything after its name, and so must we.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        opening: Opening tag of the Django comment block.
    """
    source = (
        f'<view xmlns="{HV}">{opening}<text bogus="1"/>{{% endcomment %}}'
        "<text>ok</text></view>"
    )

    validate_hyperview_schema(_render(source))
    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("literal", ["{# note\n#}", "{{ value\n}}", "{% load i18n\n%}"])
def test_validation_treats_a_delimiter_split_over_lines_as_the_text_django_renders(
    admin_client: Client, literal: str
) -> None:
    """Django's tag expression excludes line breaks, so these are literal text.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        literal: Delimiter that Django renders as plain text because it spans lines.
    """
    source = f'<view xmlns="{HV}">{literal}<text>ok</text></view>'

    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(_render(source))
    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert [item["code"] for item in result["diagnostics"]] == ["schema_structure"]
    assert result["diagnostics"][0]["message"] == (
        'Text content is not allowed inside element "view".'
    )


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_accepts_a_split_delimiter_where_text_is_allowed(
    admin_client: Client,
) -> None:
    """Validation accepts a delimiter split over lines where text is allowed.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = f'<text xmlns="{HV}">{{# note\n#}}</text>'

    validate_hyperview_schema(_render(source))
    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_a_split_delimiter_inside_a_tag_as_malformed_xml(
    admin_client: Client,
) -> None:
    """Validation reports a delimiter split inside a tag as malformed XML.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = f'<text xmlns="{HV}" {{{{ attributes\n}}}}/>'

    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(_render(source))
    result = _validate(admin_client, source)

    assert [item["code"] for item in result["diagnostics"]] == ["xml_syntax"]
    assert result["ok"] is False


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_cost_grows_linearly_with_the_number_of_django_tokens(
    admin_client: Client,
) -> None:
    """The context of each token used to be found by lexing the whole draft.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
    """
    body = "\n".join(f"<text>{{{{ v{index} }}}}</text>" for index in range(4000))
    source = f'<view xmlns="{HV}">\n{body}\n</view>'

    started = time.perf_counter()
    result = _validate(admin_client, source)

    assert result == {"ok": True, "diagnostics": []}
    assert time.perf_counter() - started < 5  # 25 s before, 0.1 s now


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (lambda: "{{\n" * 40_000, []),
        (lambda: "<?xml " * 40_000, ["xml_syntax"]),
        (lambda: "{{ a }}" + "<!--" * 40_000, ["xml_syntax"]),
        (lambda: "{{ " * 40_000, ["max_unclosed"]),
    ],
    ids=["open-braces", "open-declarations", "open-comments", "open-braces-one-line"],
)
def test_validation_scans_unterminated_constructs_in_linear_time(
    admin_client: Client, build: Callable[[], str], expected: list[str]
) -> None:
    """Unanchored lazy patterns rescanned the input for every unmatched opener.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        build: Builds the draft that repeats one unterminated construct.
        expected: Diagnostic codes the validation is expected to report.
    """
    source = build()

    started = time.perf_counter()
    result = _validate(admin_client, source)

    assert [item["code"] for item in result["diagnostics"]] == expected
    assert time.perf_counter() - started < 10  # 30 to 50 s before


def _marker_name_then_underscores_then_tokens(size: int) -> str:
    """The source that made the marker, and so the masked document, grow with it."""
    head = _static_module()._DYNAMIC_VALUE
    run = (size - len(head)) // 2
    return head + "_" * run + "{{a}}" * ((size - len(head) - run) // 5)


@pytest.mark.parametrize(
    ("size", "limit"), [(18_000, 5 * 2**20), (1_000_000, 100 * 2**20)]
)
def test_static_check_memory_does_not_depend_on_underscores_after_the_marker_name(
    size: int, limit: int
) -> None:
    """The marker used to grow with them, and every Django token carries one.

    Args:
        size: Size of the generated draft, in repeated units.
        limit: Largest memory peak the check may reach, in bytes.
    """
    module = _static_module()
    source = _marker_name_then_underscores_then_tokens(size)
    # Checked first: with the old marker the call below needs 31 MiB at 18 KB, and
    # about 47 GiB at 1 MB.
    assert len(module._unused_marker(source, module._DYNAMIC_VALUE)) < 32

    tracemalloc.start()
    try:
        result = module._static_schema_diagnostics("screens/draft.xml", source)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert result == []
    assert peak < limit


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_does_not_report_a_syntax_error_for_the_marker_amplifier() -> None:
    """The 16 MB text node that the old marker produced was refused by the parser."""
    source = _marker_name_then_underscores_then_tokens(18_000)

    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result == {"ok": True, "diagnostics": []}


def _incomplete(result: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    return [(i["code"], i["severity"], i["line"]) for i in result["diagnostics"]]


@override_settings(
    HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_BYTES": 10_000}}
)
@pytest.mark.parametrize(
    "source",
    [
        "{{a}}" * 2_000,
        # The scan that finds the include contexts is bounded as well.
        '{% include "partials/missing.xml" %}' + "{{a}}" * 1_990,
    ],
    ids=["tokens", "tokens-and-include"],
)
def test_a_masked_document_beyond_four_times_max_bytes_is_not_built(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Tokens shorter than their marker would multiply the source about 4.6 times.

    Args:
        monkeypatch: Patches the code under test for this test.
        source: Draft whose masked document would exceed the budget.
    """
    _forbid(monkeypatch, "_blank_xml_declarations")

    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result["ok"] is True
    assert _incomplete(result) == [("schema_static_incomplete", "info", None)]


@override_settings(
    HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_BYTES": 10_000}}
)
def test_a_masked_document_within_the_bound_is_checked_as_before() -> None:
    """A masked document within the bound is checked as before."""
    source = f'<view xmlns="{HV}">' + "<text>{{ v }}</text>" * 400 + "</view>"
    assert len(source) < 10_000

    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result == {"ok": True, "diagnostics": []}


@override_settings(
    HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_BYTES": 10_000}}
)
def test_text_and_comments_add_nothing_to_the_masked_document() -> None:
    """Only a token that is replaced by a marker lengthens the document."""
    source = f'<view xmlns="{HV}"><text>' + "x{##}" * 1_900 + "</text></view>"
    assert len(source) < 10_000

    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result == {"ok": True, "diagnostics": []}


@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10_000}})
@pytest.mark.parametrize(
    ("unit", "marker", "tokens"),
    [
        ("{{a}}", "DJHVSTATICDYNAMIC0", 1_700),
        ("{% a %}", "DJHVSTATICSTRUCTURE0", 1_300),
    ],
    ids=["value", "structure"],
)
def test_the_masked_document_may_be_exactly_four_times_max_bytes_long(
    unit: str, marker: str, tokens: int
) -> None:
    """The masked document may be exactly four times the maximum bytes long.

    Args:
        unit: Django token repeated to fill the masked document.
        marker: Marker that the draft must not be able to spell.
        tokens: Number of tokens repeated.
    """
    padding = 4 * 10_000 - tokens * (len(unit) + len(marker))
    module = _static_module()

    scan = module._scan_template(unit * tokens + "x" * padding)

    assert len(scan.masked) == 4 * 10_000
    with pytest.raises(module._MaskTooLarge):
        module._scan_template(unit * tokens + "x" * (padding + 1))


@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10_000}})
@pytest.mark.parametrize("wide", ["\u00e9", "\u754c", "\U0001d49c"])
@pytest.mark.parametrize("count", [100, 400])
def test_the_masked_document_budget_counts_utf8_bytes(wide: str, count: int) -> None:
    """Characters of two to four bytes used to count as one, so the text grew.

    Args:
        wide: Character that takes several UTF-8 bytes.
        count: Number of times the wide character is repeated.
    """
    module = _static_module()
    budget = 4 * 10_000
    # {{a}} is five bytes and becomes a marker of 18 bytes and five blanks.
    tokens = (budget - count * len(wide.encode())) // 23

    scan = module._scan_template("{{a}}" * tokens + wide * count)

    assert budget - 23 < len(scan.masked.encode()) <= budget
    with pytest.raises(module._MaskTooLarge):
        module._scan_template("{{a}}" * (tokens + 1) + wide * count)


@override_settings(
    HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_BYTES": 10_000}}
)
def test_the_worst_draft_for_the_masked_budget_is_reported_as_incomplete() -> None:
    """It is 9,996 bytes long, and the character count let it grow to 41,028."""
    source = "{{a}}" * 1_724 + "\U0001d49c" * 344
    assert len(source.encode()) <= 10_000

    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result["ok"] is True
    assert _incomplete(result) == [("schema_static_incomplete", "info", None)]


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("source", "messages"),
    [
        (
            f'<view xmlns="{HV}"><a{{{{ x }}}} /></view>',
            ['Element "a{...}" is not declared by the selected schema.'],
        ),
        (
            f'<view xmlns="{HV}"><text a{{{{ x }}}}="1" /></view>',
            ['Attribute "a{...}" is not allowed on element "text".'],
        ),
        (
            f'<view xmlns="{HV}"><text{{% now "Y" %}} /></view>',
            [
                'Element "text{...}" is not declared by the selected schema.',
                'Element "text{...}" is not allowed inside "view" at this position.',
            ],
        ),
    ],
    ids=["structure-in-name", "structure-in-attribute-name", "value-in-name"],
)
def test_a_token_inside_a_name_does_not_leak_a_marker_into_a_message(
    source: str, messages: list[str]
) -> None:
    """A Django token inside a name does not leak a marker into a message.

    Args:
        source: Draft with a Django token inside an element or attribute name.
        messages: Diagnostic messages the validation is expected to return.
    """
    result = _static_module().validate_draft_source("screens/draft.xml", source)

    reported = [item["message"] for item in result["diagnostics"]]
    assert all(message in reported for message in messages)
    assert not any("DJHVSTATIC" in message for message in reported)


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "source",
    [
        # A marker one above this run would have nine digits and occur in the value.
        f'<view xmlns="{HV}" scroll-orientation="DJHVSTATICDYNAMIC100000000" '
        'id="DJHVSTATICDYNAMIC99999999" />',
        f'<view xmlns="{HV}"><DJHVSTATICSTRUCTURE100000000 bad="1" />'
        "<DJHVSTATICSTRUCTURE99999999 /></view>",
    ],
    ids=["value", "structure"],
)
def test_a_draft_holding_the_largest_marker_number_is_reported_as_incomplete(
    source: str,
) -> None:
    """A draft that holds the largest marker number is reported as incomplete.

    Args:
        source: Draft that already holds the largest marker numbers.
    """
    result = _static_module().validate_draft_source("screens/draft.xml", source)

    assert result["ok"] is True
    assert _incomplete(result) == [("schema_static_incomplete", "info", None)]


def _page_with(kind: str, text: str) -> str:
    """A draft whose attribute value is the text, next to the tag the kind needs."""
    if kind == "value":
        return f'<view xmlns="{HV}" scroll-orientation="{text}" id="{{{{ id }}}}" />'
    if kind == "structure":
        return (
            f'<view xmlns="{HV}" scroll-orientation="{text}">'
            "<text>{{ x|safe }}</text></view>"
        )
    return (
        f'<view xmlns="{HV}" id="{{{{ id }}}}">'
        f'<behavior trigger="press" action="{text}" bogus="1" /></view>'
    )


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("kind", "marker", "spelled"),
    [
        ("value", "DJHVSTATICDYNAMIC0", "&#68;JHVSTATICDYNAMIC0"),
        ("value", "DJHVSTATICDYNAMIC0", "&#x44;JHVSTATICDYNAMIC&#x30;"),
        ("value", "DJHVSTATICDYNAMIC0", "&#68;&#x4a;HVSTATICDYNAMIC&#48;"),
        ("value", "DJHVSTATICDYNAMIC0", "&#0000068;JHVSTATICDYNAMIC&#x0030;"),
        (
            "value",
            "DJHVSTATICDYNAMIC0",
            "".join(f"&#{ord(char)};" for char in "DJHVSTATICDYNAMIC0"),
        ),
        ("structure", "DJHVSTATICSTRUCTURE0", "&#68;JHVSTATICSTRUCTURE0"),
        ("structure", "DJHVSTATICSTRUCTURE0", "&#x44;JHVSTATICSTRUCTURE&#x30;"),
        ("action", "DJHVSTATICDYNAMIC0", "&#68;JHVSTATICDYNAMIC0"),
    ],
    ids=[
        "value-decimal",
        "value-hexadecimal",
        "value-mixed",
        "value-zero-padded",
        "value-every-letter",
        "structure-decimal",
        "structure-hexadecimal",
        "action",
    ],
)
def test_a_character_reference_cannot_spell_a_marker(
    kind: str, marker: str, spelled: str
) -> None:
    """The parser decodes the reference, so the text is real content, not a tag.

    Args:
        kind: Kind of marker under test: value, structure or action.
        marker: Marker that the draft must not be able to spell.
        spelled: Character references that spell the marker.
    """
    module = _static_module()

    def verdict(text: str) -> tuple[bool, list[tuple[str, str, int | None]]]:
        result = module.validate_draft_source(
            "screens/draft.xml", _page_with(kind, text)
        )
        return result["ok"], _incomplete(result)

    literal = verdict(marker)

    assert literal[0] is False
    assert verdict(spelled) == literal


def test_character_references_are_decoded_once_as_the_parser_does() -> None:
    """Character references are decoded once, as the parser does."""
    decode = _static_module()._decode_character_references

    assert decode("&#68;&#x4a;&#0000072;&#x0056;") == "DJHV"
    # The parser decodes the text once, so this is a reference again after it.
    assert decode("&#38;#68;") == "&#68;"
    assert decode("&amp;#68; and no reference") == "&amp;#68; and no reference"


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("reference", "decoded"),
    [
        ("&#" + "9" * 4_000 + ";", None),
        ("&#x" + "f" * 4_000 + ";", None),
        ("&#1114112;", None),
        ("&#x110000;", None),
        ("&#9999999;", None),
        ("&#xffffff;", None),
        # Leading zeros are valid, however many, and stay one bounded number.
        ("&#" + "0" * 4_000 + "68;", "D"),
    ],
    ids=[
        "decimal-digits",
        "hexadecimal-digits",
        "above-unicode",
        "above-unicode-hexadecimal",
        "largest-decimal",
        "largest-hexadecimal",
        "zero-padded",
    ],
)
def test_an_oversized_character_reference_is_left_for_the_parser_to_refuse(
    reference: str, decoded: str | None
) -> None:
    """An oversized character reference is left for the parser to refuse.

    Args:
        reference: Character reference placed in the draft.
        decoded: Character the reference decodes to, or None when it is refused.
    """
    module = _static_module()

    assert module._decode_character_references(reference) == (decoded or reference)
    result = module.validate_draft_source(
        "screens/draft.xml", _page_with("value", reference)
    )

    assert result["ok"] is False


def test_a_scan_decodes_the_references_of_the_source_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan decodes the character references of the source only once.

    Args:
        monkeypatch: Counts how often the references of the source are decoded.
    """
    module = _static_module()
    decode = module._decode_character_references
    calls = []
    monkeypatch.setattr(
        module,
        "_decode_character_references",
        lambda text: calls.append(1) or decode(text),
    )

    module._scan_template("&#68;JHVSTATICDYNAMIC0 {{ a }} {% b %}")

    assert len(calls) == 1


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_rejects_too_many_nodes_before_the_schema_walk(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation rejects too many nodes before the schema walk.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Fails the test if the schema walk starts.
    """
    _forbid(monkeypatch, "_get_compiled_registry", "_get_static_validation_catalog")

    result = _validate(admin_client, "<x/>\n" * 80_000)

    assert result == {
        "ok": False,
        "diagnostics": [
            {
                "severity": "error",
                "code": "max_nodes",
                "message": "Document exceeds MAX_NODES.",
                "template": "screens/draft.xml",
                "coordinate_space": "source",
                "line": None,
                "column": None,
            }
        ],
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("limits", "size", "code"),
    [
        ({"MAX_NODES": 5}, 4, None),
        ({"MAX_NODES": 5}, 5, "max_nodes"),
        ({"MAX_DEPTH": 2}, 1, None),
        ({"MAX_DEPTH": 2}, 2, "max_depth"),
    ],
)
def test_validation_applies_the_configured_node_and_depth_limits(
    admin_client: Client, limits: dict[str, int], size: int, code: str | None
) -> None:
    """MAX_NODES counts the view and its texts; MAX_DEPTH counts nested views.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        limits: Validation limits configured for this case.
        size: Size of the generated draft, in repeated units.
        code: Diagnostic code the validation is expected to report, or None.
    """
    if "MAX_NODES" in limits:
        body = "<text/>" * size
    else:
        body = "<view>" * (size - 1) + "<text/>" + "</view>" * (size - 1)
    source = f'<view xmlns="{HV}">{body}</view>'

    with override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": limits}):
        result = _validate(admin_client, source)

    assert [item["code"] for item in result["diagnostics"]] == (
        [] if code is None else [code]
    )


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_maps_the_parser_depth_ceiling_to_max_depth(
    admin_client: Client,
) -> None:
    """Validation maps the parser depth ceiling to the max_depth code.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = "<view>" * 300 + "</view>" * 300

    result = _validate(admin_client, source)

    assert [item["code"] for item in result["diagnostics"]] == ["max_depth"]
    assert result["ok"] is False


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("attributes", [129, 200])
def test_validation_caps_the_attributes_of_one_element_before_the_schema_walk(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, attributes: int
) -> None:
    """Validation caps the attributes of one element before the schema walk.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Patches the code under test for this test.
        attributes: Number of attributes on the element.
    """
    _forbid(monkeypatch, "_get_compiled_registry", "_get_static_validation_catalog")
    names = " ".join(f"a{index}='1'" for index in range(attributes))

    result = _validate(admin_client, f"<view {HYPERVIEW_XMLNS} {names} />")

    assert [(item["code"], item["message"]) for item in result["diagnostics"]] == [
        ("max_attributes", "Element exceeds 128 attributes.")
    ]
    assert "a0" not in json.dumps(result)


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("elements", [10, 100])
def test_validation_rejects_a_thousand_undeclared_attributes_before_any_structure_walk(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, elements: int
) -> None:
    """Lax iteration took 10 s for 10 such elements and 97 s for 100 of them.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Fails the test if the structure walk starts.
        elements: Number of elements in the draft.
    """
    _forbid(
        monkeypatch,
        "_get_compiled_registry",
        "_get_static_validation_catalog",
        "_static_structure_diagnostics",
    )
    names = " ".join(f"a{index}='1'" for index in range(1000))
    source = f'<view xmlns="{HV}">' + f"<text {names}/>\n" * elements + "</view>"

    started = time.perf_counter()
    result = _validate(admin_client, source)

    assert [(item["code"], item["message"]) for item in result["diagnostics"]] == [
        ("max_attributes", "Element exceeds 128 attributes.")
    ]
    assert time.perf_counter() - started < 5


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_lets_an_element_with_exactly_the_attribute_cap_reach_the_schema(
    admin_client: Client,
) -> None:
    """An element with exactly the attribute cap still reaches the schema check.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    names = " ".join(f"a{index}='1'" for index in range(128))

    result = _validate(admin_client, f"<view {HYPERVIEW_XMLNS} {names} />")

    assert {item["code"] for item in result["diagnostics"][:50]} == {"schema_attribute"}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(8)} />",
            "Element exceeds 8 in-scope namespaces.",
        ),
        (
            rebinding_path(40),
            "Element exceeds 32 namespace declarations on its ancestor path.",
        ),
    ],
)
def test_validation_caps_namespaces_before_the_schema_walk(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, source: str, message: str
) -> None:
    """Validation caps the namespaces before the schema walk.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Patches the code under test for this test.
        source: Draft that declares too many namespaces.
        message: Diagnostic message the validation is expected to report.
    """
    _forbid(monkeypatch, "_get_compiled_registry")

    result = _validate(admin_client, source)

    assert [(item["code"], item["message"]) for item in result["diagnostics"]] == [
        ("max_attributes", message)
    ]


@pytest.mark.django_db
def test_validation_rejects_a_flood_of_namespace_declarations_on_one_path(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1,008 declarations on a path took seconds in the schema walk.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Fails the test if the schema walk starts.
    """
    _forbid(monkeypatch, "_get_compiled_registry")

    with override_settings(
        HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_DEPTH": 256}}
    ):
        started = time.perf_counter()
        result = _validate(admin_client, rebinding_path(1008))

    assert [item["code"] for item in result["diagnostics"]] == ["max_attributes"]
    assert time.perf_counter() - started < 5


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_returns_a_bounded_list_with_a_final_omission_note(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation returns a bounded list that ends with an omission note.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Fails the test if the structure check runs.
    """
    _forbid(monkeypatch, "_static_structure_diagnostics")

    result = _validate(admin_client, "<x/>\n" * 5000)

    assert result["ok"] is False
    assert len(result["diagnostics"]) == 51
    assert {item["code"] for item in result["diagnostics"][:50]} == {"schema_element"}
    assert result["diagnostics"][50] == {
        "severity": "info",
        "code": "diagnostics_truncated",
        "message": "More diagnostics were omitted.",
        "template": None,
        "coordinate_space": None,
        "line": None,
        "column": None,
    }
    assert len(json.dumps(result)) < 20_000


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_stops_listing_after_the_limit_whatever_the_element_count(
    admin_client: Client,
) -> None:
    """850 elements with 128 undeclared attributes gave 108,800 diagnostics.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
    """
    names = " ".join(f"a{index}='1'" for index in range(128))
    source = f'<view xmlns="{HV}">' + f"<text {names}/>\n" * 850 + "</view>"

    result = _validate(admin_client, source)

    assert len(result["diagnostics"]) == 51
    assert result["diagnostics"][-1]["code"] == "diagnostics_truncated"


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_keeps_errors_ahead_of_notes_when_it_truncates(
    admin_client: Client,
) -> None:
    """Validation keeps errors ahead of notes when it truncates the list.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = "<x/>\n" * 60 + "{% if a %}<text/>{% endif %}"

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert [item["severity"] for item in result["diagnostics"]] == [
        *["error"] * 50,
        "info",
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "expression",
    ["fragment|safe", "fragment | safe", "fragment|default:''|safe|upper"],
)
def test_validation_defers_markup_injected_by_a_safe_variable(
    admin_client: Client, expression: str
) -> None:
    """A variable marked safe may render elements, so its position is not an error.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        expression: Variable expression that marks its value as safe.
    """
    source = f'<screen xmlns="{HV}">{{{{ {expression} }}}}<body /></screen>'

    result = _validate(admin_client, source)

    assert result["ok"] is True
    assert [item["code"] for item in result["diagnostics"]] == [
        "schema_static_incomplete"
    ]
    assert result["diagnostics"][0]["severity"] == "info"


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "expression", ["label", "label|upper", "items|safeseq", "label|default:'safe'"]
)
def test_validation_still_rejects_ordinary_text_variables_in_element_only_content(
    admin_client: Client, expression: str
) -> None:
    """Validation still rejects ordinary text variables where only elements fit.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        expression: Variable expression whose value is ordinary text.
    """
    source = f'<screen xmlns="{HV}">{{{{ {expression} }}}}<body /></screen>'

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert [item["code"] for item in result["diagnostics"]] == ["schema_structure"]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_does_not_defer_a_safe_variable_in_an_attribute_value(
    admin_client: Client,
) -> None:
    """A safe variable in an attribute value is not deferred.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = f'<view xmlns="{HV}" scroll-orientation="{{{{ direction|safe }}}}" />'

    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_excessive_nesting_instead_of_failing(
    admin_client: Client, finalized_garbage: None
) -> None:
    """Validation reports excessive nesting instead of failing.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        finalized_garbage: Collects pending garbage before the deep recursion starts.
    """
    result = _validate(admin_client, NESTED_IF)

    assert result == {
        "ok": False,
        "diagnostics": [
            {
                "severity": "error",
                "code": "too_deep",
                "message": "Template is nested too deeply.",
                "template": "screens/draft.xml",
                "coordinate_space": "source",
                "line": None,
                "column": None,
            }
        ],
    }


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_nesting_that_rendering_cannot_survive_as_too_deep(
    admin_client: Client,
) -> None:
    """The draft compiles, but rendering recurses three frames for every level.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
    """
    from dj_hyperview.contrib.database._template_syntax import _MAX_NESTING

    within = f"<view xmlns='{HV}'>" + nested("if", _MAX_NESTING) + "</view>"
    over = f"<view xmlns='{HV}'>" + nested("if", _MAX_NESTING + 1) + "</view>"

    assert [
        item["code"] for item in _validate(admin_client, within)["diagnostics"]
    ] == ["schema_static_incomplete"]
    assert [item["code"] for item in _validate(admin_client, over)["diagnostics"]] == [
        "too_deep"
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("tag", NESTED_BLOCKS)
def test_validation_reports_deep_nesting_as_too_deep_whatever_the_opening_tag(
    admin_client: Client, tag: str
) -> None:
    """Filters in the opening tags used to end the request with HTTP 500.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        tag: Opening tag of the Django block that is nested too deeply.
    """
    source = f"<view xmlns='{HV}'>" + nested(tag, 600) + "</view>"

    result = _validate(admin_client, source)

    assert [(item["code"], item["template"]) for item in result["diagnostics"]] == [
        ("too_deep", "screens/draft.xml")
    ]
    assert _static_module().validate_draft_source("screens/draft.xml", source) == result


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_admin_save_reports_excessive_nesting_on_the_content_field(
    admin_client: Client, finalized_garbage: None
) -> None:
    """Saving reports excessive nesting on the content field.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        finalized_garbage: Collects pending garbage before the deep recursion starts.
    """
    _, model = _admin_types()

    response = admin_client.post(
        reverse("admin:dj_hyperview_database_hyperviewtemplate_add"),
        {
            "name": "screens/deep.xml",
            "content": NESTED_IF,
            "active": "on",
            "_save": "Save",
        },
    )

    errors = response.context["adminform"].form.errors.as_data()["content"]
    assert response.status_code == 200
    assert [error.code for error in errors] == ["too_deep"]
    assert str(errors[0].message) == "Template is nested too deeply."
    assert model.objects.count() == 0


@pytest.mark.django_db
def test_admin_save_reports_excessive_nesting_without_the_editor(
    admin_client: Client, finalized_garbage: None
) -> None:
    """The model validator covers the standard textarea, which has no editor checks.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        finalized_garbage: Collects pending garbage before the deep recursion starts.
    """
    _, model = _admin_types()

    response = admin_client.post(
        reverse("admin:dj_hyperview_database_hyperviewtemplate_add"),
        {
            "name": "screens/deep.xml",
            "content": NESTED_IF,
            "active": "on",
            "_save": "Save",
        },
    )

    errors = response.context["adminform"].form.errors.as_data()["content"]
    assert response.status_code == 200
    assert [error.code for error in errors] == ["too_deep"]
    assert model.objects.count() == 0


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_stops_the_structure_check_at_the_diagnostic_limit(
    admin_client: Client,
) -> None:
    """Validation stops the structure check at the diagnostic limit.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
    """
    source = f'<doc xmlns="{HV}">' + "<screen><body/><body/></screen>\n" * 60 + "</doc>"

    result = _validate(admin_client, source)

    assert result["ok"] is False
    assert len(result["diagnostics"]) == 51
    assert {item["code"] for item in result["diagnostics"][:50]} == {"schema_structure"}
    assert result["diagnostics"][50]["code"] == "diagnostics_truncated"


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_reports_recursion_in_the_static_stage_as_too_deep(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation reports recursion in the static stage as too deep.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Makes the static stage exhaust the recursion limit.
    """

    def exhausted(name: str, content: str) -> Never:
        raise RecursionError

    monkeypatch.setattr(_static_module(), "_static_schema_diagnostics", exhausted)

    result = _validate(admin_client, "<view />")

    assert [item["code"] for item in result["diagnostics"]] == ["too_deep"]


@pytest.mark.django_db
def test_literal_includes_are_not_compiled_by_djangos_template_class(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Template uses Django's lexer, which is quadratic: 40,000 openers took 15 s.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Fails the test if Django compiles an include.
        tmp_path: Temporary directory that holds the files of the test.
    """

    def forbidden(self: Any) -> None:
        raise AssertionError("an include target was compiled by Template")

    monkeypatch.setattr("django.template.base.Template.compile_nodelist", forbidden)
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "label.xml").write_text("<text>{{ label }}</text>", encoding="utf-8")
    (partials / "openers.xml").write_text("<view>" + "{{ " * 40_000, encoding="utf-8")
    source = f'<view xmlns="{HV}">{{% include "partials/label.xml" %}}</view>'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)
        unclosed = _validate(admin_client, '{% include "partials/openers.xml" %}')

    assert result == {"ok": True, "diagnostics": []}
    assert unclosed["ok"] is False
    assert [item["code"] for item in unclosed["diagnostics"]] == ["max_unclosed"]


def _bounded_includes_config(tmp_path: Path, max_bytes: int) -> dict[str, Any]:
    return {
        **_filesystem_admin_config(tmp_path),
        "VALIDATION": {"MAX_BYTES": max_bytes},
    }


@pytest.mark.django_db
def test_include_expansion_stops_when_the_composed_source_exceeds_max_bytes(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """64 includes of a 990 KB template used 3.3 GB; the composed size is bounded.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Counts the template resolutions of the draft and its includes.
        tmp_path: Temporary directory that holds the files of the test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "big.xml").write_text(f"<text>{'x' * 400}</text>", encoding="utf-8")
    calls = _count_resolutions(monkeypatch)
    source = f'<view xmlns="{HV}">' + '{% include "partials/big.xml" %}' * 6 + "</view>"

    with override_settings(HYPERVIEW=_bounded_includes_config(tmp_path, 1000)):
        result = _validate(admin_client, source)

    assert len(calls) == 2
    assert result["ok"] is True
    assert [(item["code"], item["severity"]) for item in result["diagnostics"]] == [
        ("schema_static_incomplete", "info")
    ]


@pytest.mark.django_db
def test_include_size_limit_covers_nested_includes_with_one_note(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The include size limit covers nested includes and adds one note.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        monkeypatch: Counts the template resolutions of the draft and its includes.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "leaf.xml").write_text(f"<text>{'x' * 400}</text>", encoding="utf-8")
    (partials / "inner.xml").write_text(
        '{% include "partials/leaf.xml" %}' * 3, encoding="utf-8"
    )
    calls = _count_resolutions(monkeypatch)
    source = (
        f'<view xmlns="{HV}">' + '{% include "partials/inner.xml" %}' * 2 + "</view>"
    )

    with override_settings(HYPERVIEW=_bounded_includes_config(tmp_path, 1000)):
        result = _validate(admin_client, source)

    assert calls == ["partials/inner.xml", "partials/leaf.xml", "partials/leaf.xml"]
    assert result["ok"] is True
    assert sorted(
        (item["template"], item["code"]) for item in result["diagnostics"]
    ) == [
        ("partials/inner.xml", "schema_static_incomplete"),
        ("screens/draft.xml", "schema_static_incomplete"),
    ]


@pytest.mark.django_db
def test_include_syntax_error_is_reported_for_the_target_and_expansion_goes_on(
    admin_client: Client, tmp_path: Path
) -> None:
    """An include syntax error is reported for its target and expansion continues.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "bad.xml").write_text("{% if ready %}<text />", encoding="utf-8")
    (partials / "bad_style.xml").write_text(
        f'<style xmlns="{HV}" id="card" kk="1" />', encoding="utf-8"
    )
    source = '{% include "partials/bad.xml" %}{% include "partials/bad_style.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert [(item["template"], item["code"]) for item in result["diagnostics"]] == [
        ("partials/bad.xml", "django_syntax"),
        ("partials/bad_style.xml", "schema_attribute"),
        ("screens/draft.xml", "schema_static_incomplete"),
    ]


@pytest.mark.django_db
def test_include_nested_too_deeply_is_reported_for_the_target_and_expansion_goes_on(
    admin_client: Client, tmp_path: Path
) -> None:
    """An include nested too deeply is reported for its target and expansion continues.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        tmp_path: Temporary directory that holds the template files and schemas of the
        test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "deep.xml").write_text(nested("if-filter", 101), encoding="utf-8")
    (partials / "bad_style.xml").write_text(
        f'<style xmlns="{HV}" id="card" kk="1" />', encoding="utf-8"
    )
    source = '{% include "partials/deep.xml" %}{% include "partials/bad_style.xml" %}'

    with override_settings(HYPERVIEW=_filesystem_admin_config(tmp_path)):
        result = _validate(admin_client, source)

    assert [(item["template"], item["code"]) for item in result["diagnostics"]] == [
        ("partials/deep.xml", "too_deep"),
        ("partials/bad_style.xml", "schema_attribute"),
        ("screens/draft.xml", "schema_static_incomplete"),
    ]
    assert result["ok"] is False


@pytest.mark.django_db
def test_include_expansion_validates_the_source_of_each_resolved_target(
    admin_client: Client, tmp_path: Path
) -> None:
    """The include is checked against the same limits as the draft, before compiling.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        tmp_path: Temporary directory that holds the files of the test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "big.xml").write_text(f"<text>{'x' * 1500}</text>", encoding="utf-8")
    source = f'<view xmlns="{HV}">{{% include "partials/big.xml" %}}</view>'

    with override_settings(HYPERVIEW=_bounded_includes_config(tmp_path, 1000)):
        result = _validate(admin_client, source)

    assert result["ok"] is False
    assert [(item["code"], item["template"]) for item in result["diagnostics"]] == [
        ("max_bytes", "screens/draft.xml")
    ]


@pytest.mark.django_db
def test_include_size_limit_counts_bytes_not_characters(
    admin_client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Counting characters would let multi-byte text reach 4 x MAX_BYTES.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        monkeypatch: Counts the template resolutions of the draft and its includes.
        tmp_path: Temporary directory that holds the files of the test.
    """
    partials = tmp_path / "partials"
    partials.mkdir()
    (partials / "wide.xml").write_text(f"<text>{'é' * 400}</text>", encoding="utf-8")
    calls = _count_resolutions(monkeypatch)
    source = (
        f'<view xmlns="{HV}">' + '{% include "partials/wide.xml" %}' * 6 + "</view>"
    )

    with override_settings(HYPERVIEW=_bounded_includes_config(tmp_path, 1000)):
        result = _validate(admin_client, source)

    assert len(calls) == 1
    assert [(item["code"], item["severity"]) for item in result["diagnostics"]] == [
        ("schema_static_incomplete", "info")
    ]


NAMESPACE_BOUNDARY_PASSES = [
    pytest.param(rebinding_path(32), id="32-declarations"),
    pytest.param(
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(7)}><text>ok</text></view>",
        id="8-in-scope",
    ),
]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("source", NAMESPACE_BOUNDARY_PASSES)
def test_validation_accepts_namespaces_exactly_at_the_runtime_caps(
    admin_client: Client, source: str
) -> None:
    """The synthetic root adds a declaration and a prefix that the document lacks.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        source: Draft that sits exactly at the namespace caps.
    """
    assert _validate(admin_client, source) == {"ok": True, "diagnostics": []}
    assert validate_hxml(source) == source


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            rebinding_path(33),
            "Element exceeds 32 namespace declarations on its ancestor path.",
        ),
        (
            f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(8)}><text>ok</text></view>",
            "Element exceeds 8 in-scope namespaces.",
        ),
    ],
)
def test_validation_rejects_namespaces_one_over_the_runtime_caps(
    admin_client: Client, source: str, message: str
) -> None:
    """Validation rejects a draft that is one namespace over the runtime caps.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        source: Draft that is one namespace over the cap.
        message: Diagnostic message the validation is expected to report.
    """
    result = _validate(admin_client, source)

    assert [(item["code"], item["message"]) for item in result["diagnostics"]] == [
        ("max_attributes", message)
    ]


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_validation_counts_namespaces_per_top_level_element(
    admin_client: Client,
) -> None:
    """Sibling top-level elements, as includes make them, do not add up.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
    """
    sibling = f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(7)}><text>ok</text></view>"

    assert _validate(admin_client, sibling * 3) == {"ok": True, "diagnostics": []}


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize(
    "source",
    [
        f'<doc xmlns="{HV}"><screen>{{% verbatim %}}<body/>{{% endverbatim %}}'
        "</screen></doc>",
        f'<body xmlns="{HV}">{{% verbatim %}}<view>{{% endverbatim %}}</view></body>',
    ],
    ids=["required-child", "split-tag"],
)
def test_validation_defers_markup_that_a_verbatim_body_supplies(
    admin_client: Client, source: str
) -> None:
    """A verbatim body is not analyzed, so what it may supply gets the note.

    Args:
        admin_client: Client signed in as a superuser that validates drafts.
        source: Draft whose verbatim body may supply markup.
    """
    result = _validate(admin_client, source)

    assert result["ok"] is True
    assert [(item["code"], item["severity"]) for item in result["diagnostics"]] == [
        ("schema_static_incomplete", "info")
    ]
    validate_hxml(_render(source))


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_the_static_pass_stops_collecting_element_findings_past_its_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The static pass stops collecting element findings past its limit.

    Args:
        monkeypatch: Counts the diagnostics the static pass builds.
    """
    module = _static_module()
    created: list[str] = []
    original = module._diagnostic

    def counting(code: str, *args: object, **kwargs: object) -> Any:
        created.append(code)
        return original(code, *args, **kwargs)

    monkeypatch.setattr(module, "_diagnostic", counting)

    found = module._static_schema_diagnostics("a.xml", "<x/>\n" * 5000)

    assert len(found) == created.count("schema_element") == module._MAX_DIAGNOSTICS + 1


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
@pytest.mark.parametrize("limit", [1, 3, 10, 100])
def test_the_structure_check_stops_at_the_limit_it_is_given(limit: int) -> None:
    """The structure check stops at the limit it is given.

    Args:
        limit: Maximum number of diagnostics the check may return.
    """
    from lxml import etree

    module = _static_module()
    root = etree.fromstring(
        f'<r xmlns="{HV}">' + "<screen><body/><body/></screen>\n" * 10 + "</r>"
    )

    found = module._static_structure_diagnostics(
        "a.xml", root, module._get_compiled_registry(), limit
    )

    assert len(found) == min(limit, 10)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("depth", "limit"),
    [(300, 64), (9, 8)],
    ids=["parser-ceiling", "configured-limit"],
)
def test_validation_words_the_depth_limit_like_rendering(
    admin_client: Client, depth: int, limit: int
) -> None:
    """Validation words the depth limit as rendering does.

    Args:
        admin_client: Client signed in as a superuser that posts drafts to the
        validation endpoint.
        depth: Nesting depth of the draft.
        limit: Depth limit the message is expected to name.
    """
    source = "<view>" * depth + "</view>" * depth

    with override_settings(
        HYPERVIEW={"ADMIN": {"EDITOR": True}, "VALIDATION": {"MAX_DEPTH": limit}}
    ):
        result = _validate(admin_client, source)

    assert [(item["code"], item["message"]) for item in result["diagnostics"]] == [
        ("max_depth", "Document exceeds MAX_DEPTH.")
    ]
