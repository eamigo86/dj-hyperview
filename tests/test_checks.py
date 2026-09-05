from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings


@override_settings(HYPERVIEW={})
def test_minimal_installation_requires_no_optional_services() -> None:
    assert check_hyperview_settings() == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"TEMPLATE_DIRS": "hyperview"}, ["E002"]),
        ({"TEMPLATE_DIRS": [object(), "/missing/dj-hyperview"]}, ["E002"] * 2),
        ({"SOURCES": {}}, ["E003"]),
        ({"SOURCES": [None]}, ["E003"]),
        ({"SOURCES": [{"BACKEND": 1, "OPTIONS": []}]}, ["E003"] * 2),
        ({"SOURCES": [{"BACKEND": "missing.Source"}]}, ["E003"]),
        ({"CACHE": []}, ["E004"]),
        ({"CACHE": {"ALIAS": "missing"}}, ["E004"]),
        ({"CACHE": {"TTL": 0}}, ["E005"]),
        ({"CACHE": {"FAILURE_MODE": "swallow"}}, ["E006"]),
        ({"VALIDATION": []}, ["E007"]),
        ({"VALIDATION": {"MODE": "sometimes"}}, ["E007"]),
        ({"VALIDATION": {"SCHEMA": object()}}, ["E008"]),
        ({"VALIDATION": {"MAX_BYTES": -1}}, ["E009"]),
        ({"VALIDATION": {"MAX_DEPTH": 257}}, ["E009"]),
    ],
)
def test_invalid_settings_return_actionable_checks(value, expected) -> None:
    with override_settings(HYPERVIEW=value):
        errors = check_hyperview_settings()

    assert [error.id.rsplit(".", 1)[-1] for error in errors] == expected
    assert all(error.hint and error.obj == "settings.HYPERVIEW" for error in errors)


def test_complete_valid_settings_pass_checks(tmp_path) -> None:
    schema = tmp_path / "schema.xsd"
    schema.touch()
    value = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        "CACHE": {"ALIAS": "screens"},
        "VALIDATION": {"SCHEMA": schema},
    }
    caches = {"screens": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    for configured_schema in (schema, "tests.stubs.validate_schema"):
        value["VALIDATION"]["SCHEMA"] = configured_schema
        with override_settings(HYPERVIEW=value, CACHES=caches):
            assert check_hyperview_settings() == []


def test_source_and_template_directory_mismatches_emit_warnings(tmp_path) -> None:
    """Checks expose inert roots and filesystem sources without roots."""
    filesystem = "dj_hyperview.sources.FileSystemSource"
    cases = [
        ({"TEMPLATE_DIRS": [tmp_path]}, "W002"),
        ({"SOURCES": [{"BACKEND": filesystem}]}, "W003"),
    ]

    for configured, expected in cases:
        with override_settings(HYPERVIEW=configured):
            messages = check_hyperview_settings()
        assert [message.id.rsplit(".", 1)[-1] for message in messages] == [expected]

    configured = {
        "SOURCES": [
            {"BACKEND": filesystem, "OPTIONS": {"template_dirs": [tmp_path]}}
        ]
    }
    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []


@pytest.mark.parametrize("template_dirs", ["/tmp/hyperview", Path("/tmp/hyperview")])
def test_filesystem_source_options_reject_scalar_template_roots(template_dirs) -> None:
    """Checks reject values that FileSystemSource cannot treat as root lists."""
    configured = {
        "SOURCES": [
            {
                "BACKEND": "dj_hyperview.sources.FileSystemSource",
                "OPTIONS": {"template_dirs": template_dirs},
            }
        ]
    }

    with override_settings(HYPERVIEW=configured):
        messages = check_hyperview_settings()

    assert [message.id for message in messages] == ["dj_hyperview.E002"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("invalid", "E001"),
        (
            {
                "SOURCES": [
                    {"BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource"}
                ]
            },
            "E010",
        ),
    ],
)
def test_root_and_optional_database_config_are_validated(value, expected) -> None:
    with override_settings(HYPERVIEW=value):
        errors = check_hyperview_settings()

    assert [error.id for error in errors] == [f"dj_hyperview.{expected}"]
