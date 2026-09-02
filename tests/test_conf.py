import pytest
from django.test import override_settings

from dj_hyperview import HyperviewConfigurationError
from dj_hyperview.conf import (
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
    }
    caches = {"screens": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    with override_settings(HYPERVIEW=value, CACHES=caches):
        config = get_settings()

    assert config == HyperviewSettings(
        template_dirs=(tmp_path,),
        sources=(SourceSettings("tests.stubs.TemplateSource", {"x": 1}),),
        cache=CacheSettings("screens", 60, 5, "raise"),
        validation=ValidationSettings("render", schema, 2_000, 8, 100),
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
