"""Preview configuration is explicit, validated, and immutable."""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError


def example_context(request, template_name):
    return {"title": template_name}


@pytest.fixture(autouse=True)
def editor_installed():
    apps = list(settings.INSTALLED_APPS)
    if "django_ace" not in apps:
        apps.append("django_ace")
    with override_settings(INSTALLED_APPS=apps):
        yield


def config(preview, *, editor=True):
    return {"ADMIN": {"EDITOR": editor, "PREVIEW": preview}}


def test_defaults_include_disabled_preview_and_empty_scenario():
    with override_settings(HYPERVIEW={}):
        preview = get_settings().admin.preview
    assert preview.enabled is False
    assert list(preview.scenarios) == ["empty"]
    assert preview.scenarios["empty"].label == "No example data"
    assert preview.scenarios["empty"].context == {}
    assert preview.scenarios["empty"].root_template is None
    with pytest.raises(TypeError):
        preview.scenarios["x"] = preview.scenarios["empty"]
    with pytest.raises(TypeError):
        preview.scenarios["empty"].context["secret"] = 1


def test_scenarios_preserve_order_and_resolve_trusted_provider():
    raw = config(
        {
            "ENABLED": True,
            "SCENARIOS": {
                "fixture": {"LABEL": "Fixture", "CONTEXT": {"title": "Hello"}},
                "user": {
                    "LABEL": "User",
                    "CONTEXT": f"{__name__}.example_context",
                    "ROOT_TEMPLATE": "screens/wrapper.xml",
                },
            },
        }
    )
    with override_settings(HYPERVIEW=raw):
        preview = get_settings().admin.preview
        assert list(preview.scenarios) == ["fixture", "user"]
        assert preview.scenarios["user"].context is example_context
        assert preview.scenarios["user"].root_template == "screens/wrapper.xml"
        with pytest.raises(TypeError):
            preview.scenarios["fixture"].context["title"] = "Changed"


@pytest.mark.parametrize(
    "preview",
    [
        None,
        [],
        True,
        {"ENABLED": "true"},
        {"SCENARIOS": []},
        {"SCENARIOS": {"": {"LABEL": "X", "CONTEXT": {}}}},
        {"SCENARIOS": {7: {"LABEL": "X", "CONTEXT": {}}}},
        {"SCENARIOS": {"x": None}},
        {"SCENARIOS": {"x": {"LABEL": "", "CONTEXT": {}}}},
        {"SCENARIOS": {"x": {"LABEL": 3, "CONTEXT": {}}}},
        {"SCENARIOS": {"x": {"CONTEXT": {}}}},
        {"SCENARIOS": {"x": {"LABEL": "X", "CONTEXT": []}}},
        {"SCENARIOS": {"x": {"LABEL": "X", "CONTEXT": "missing.provider"}}},
        {"SCENARIOS": {"x": {"LABEL": "X", "CONTEXT": lambda: {}}}},
        {
            "SCENARIOS": {
                "x": {"LABEL": "X", "CONTEXT": {}, "ROOT_TEMPLATE": "../bad.xml"}
            }
        },
        {"SCENARIOS": {"x": {"LABEL": "X", "CONTEXT": {}, "ROOT_TEMPLATE": 4}}},
    ],
)
def test_invalid_preview_configuration_fails_closed(preview):
    with override_settings(HYPERVIEW=config(preview)):
        errors = [item for item in check_hyperview_settings() if item.level >= 40]
        assert errors and all(item.id == "dj_hyperview.E020" for item in errors)
        with pytest.raises(HyperviewConfigurationError, match="E020"):
            get_settings()


def test_enabled_preview_requires_editor_and_schema_dependency():
    with override_settings(HYPERVIEW=config({"ENABLED": True}, editor=False)):
        assert any(
            item.id == "dj_hyperview.E020" for item in check_hyperview_settings()
        )
    with override_settings(HYPERVIEW=config({"ENABLED": True})):
        from dj_hyperview import checks

        original = checks.find_spec
        with patch.object(
            checks,
            "find_spec",
            side_effect=lambda name: None if name == "xmlschema" else original(name),
        ):
            assert any(
                item.id == "dj_hyperview.E020" for item in check_hyperview_settings()
            )


def test_changed_settings_replace_scenarios_and_empty_mapping_uses_default():
    with override_settings(HYPERVIEW=config({"ENABLED": True, "SCENARIOS": {}})):
        first = get_settings().admin.preview
        assert list(first.scenarios) == ["empty"]
    with override_settings(
        HYPERVIEW=config(
            {"SCENARIOS": {"next": {"LABEL": "Next", "CONTEXT": example_context}}}
        )
    ):
        second = get_settings().admin.preview
        assert list(second.scenarios) == ["next"]
        assert second is not first
