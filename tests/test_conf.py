from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import override_settings
from django.test.signals import setting_changed

from dj_hyperview import HyperviewConfigurationError
from dj_hyperview.conf import (
    AdminSettings,
    CacheSettings,
    HyperviewSettings,
    SourceSettings,
    ValidationSettings,
    get_settings,
)


@override_settings(HYPERVIEW={})
def test_settings_use_safe_optional_defaults() -> None:
    assert get_settings() == HyperviewSettings()


def test_settings_normalize_a_complete_configuration(tmp_path) -> None:
    def schema(document: str) -> None:
        del document

    (tmp_path / "extension.xsd").write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>',
        encoding="utf-8",
    )
    value = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"x": 1}}],
        "CACHE": {
            "ALIAS": "screens",
            "TTL": 60,
            "NEGATIVE_TTL": 5,
            "FAILURE_MODE": "raise",
        },
        "VALIDATION": {
            "MODE": "render",
            "SCHEMA": schema,
            "MAX_BYTES": 2_000,
            "MAX_DEPTH": 8,
            "MAX_NODES": 100,
        },
        "ADMIN": {
            "EDITOR": True,
            "PERMISSION": "tests.stubs.allow_template_admin",
        },
        "EXTRA_SCHEMAS": [tmp_path / "extension.xsd"],
    }
    caches = {"screens": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    with override_settings(
        HYPERVIEW=value,
        CACHES=caches,
        INSTALLED_APPS=[*django_settings.INSTALLED_APPS, "django_ace"],
    ):
        config = get_settings()

    assert config == HyperviewSettings(
        template_dirs=(tmp_path,),
        sources=(SourceSettings("tests.stubs.TemplateSource", {"x": 1}),),
        cache=CacheSettings("screens", 60, 5, "raise"),
        validation=ValidationSettings("render", schema, 2_000, 8, 100),
        admin=AdminSettings(
            editor=True,
            permission=__import__(
                "tests.stubs", fromlist=["allow_template_admin"]
            ).allow_template_admin,
        ),
        extra_schemas=(tmp_path / "extension.xsd",),
    )


@pytest.mark.parametrize(
    ("cache", "issues"),
    [
        ({"TTL": 0}, ("dj_hyperview.E005: CACHE.TTL must be an integer >= 1.",)),
        (
            {"TTL": 0, "FAILURE_MODE": "swallow"},
            (
                "dj_hyperview.E005: CACHE.TTL must be an integer >= 1.",
                "dj_hyperview.E006: CACHE.FAILURE_MODE is invalid.",
            ),
        ),
    ],
)
def test_invalid_settings_raise_stable_error(cache, issues) -> None:
    with override_settings(HYPERVIEW={"CACHE": cache}):
        with pytest.raises(HyperviewConfigurationError) as captured:
            get_settings()

    assert captured.value.issues == issues
    assert (
        str(captured.value) == f"Invalid HYPERVIEW configuration: {'; '.join(issues)}"
    )


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"content": "x"}}
        ]
    }
)
def test_settings_snapshot_is_cached_and_source_options_are_immutable() -> None:
    """Repeated reads reuse one validated immutable settings snapshot."""
    with patch(
        "dj_hyperview.checks.check_hyperview_settings",
        wraps=__import__(
            "dj_hyperview.checks", fromlist=["check_hyperview_settings"]
        ).check_hyperview_settings,
    ) as check:
        first = get_settings()
        second = get_settings()

    assert first is second
    assert check.call_count == 1
    with pytest.raises(TypeError):
        first.sources[0].options["content"] = "changed"


def test_settings_snapshot_is_replaced_when_hyperview_changes() -> None:
    """Django setting overrides cannot observe a stale normalized snapshot."""
    with override_settings(HYPERVIEW={}):
        first = get_settings()
    with override_settings(HYPERVIEW={"VALIDATION": {"MODE": "render"}, "SOURCES": []}):
        second = get_settings()

    assert second is not first
    assert second.validation.mode == "render"


@pytest.mark.parametrize("setting", ["CACHES", "DATABASES", "INSTALLED_APPS"])
@pytest.mark.filterwarnings("ignore:Overriding setting DATABASES can lead")
@override_settings(HYPERVIEW={})
def test_settings_snapshot_tracks_check_dependencies(setting: str) -> None:
    """Settings that affect configuration checks invalidate their cached result."""
    first = get_settings()

    setting_changed.send(sender=object, setting=setting, value=None, enter=True)

    assert get_settings() is not first
