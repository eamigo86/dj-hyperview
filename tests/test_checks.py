from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.resolver import TemplateResolver


@pytest.mark.parametrize("profile", ["unknown", None, True, [], {}])
def test_invalid_schema_profile_returns_e019(profile: object) -> None:
    """Invalid profile values are deterministic system-check errors."""
    with override_settings(HYPERVIEW={"SCHEMA_PROFILE": profile}):
        messages = check_hyperview_settings()
    assert [message.id for message in messages] == ["dj_hyperview.E019"]
    assert messages[0].hint and "SCHEMA_PROFILE" in messages[0].msg


@pytest.mark.parametrize("mode", ["raise", "bypass"])
@pytest.mark.parametrize("entrypoint", ["checks", "settings", "resolver"])
@pytest.mark.parametrize(
    "backend",
    [
        "django.core.cache.backends.filebased.FileBasedCache",
        "tests.test_runtime_cache_backend.ConsumerFileCache",
    ],
)
def test_filebased_cache_rejected_by_configuration_even_in_bypass(
    tmp_path: Path, mode: str, entrypoint: str, backend: str
) -> None:
    """Unsafe generation backends are configuration errors, never read bypasses."""
    from dj_hyperview.conf import get_settings
    from dj_hyperview.exceptions import HyperviewConfigurationError

    caches = {
        "files": {
            "BACKEND": backend,
            "LOCATION": str(tmp_path),
        }
    }
    configured = {"CACHE": {"ALIAS": "files", "FAILURE_MODE": mode}}
    with override_settings(CACHES=caches, HYPERVIEW=configured):
        if entrypoint == "checks":
            messages = check_hyperview_settings()
            assert [message.id for message in messages] == ["dj_hyperview.E018"]
        else:
            with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E018"):
                (
                    get_settings
                    if entrypoint == "settings"
                    else TemplateResolver.from_settings
                )()


@override_settings(HYPERVIEW={})
def test_minimal_installation_requires_no_optional_services() -> None:
    messages = check_hyperview_settings()

    assert [message.id for message in messages] == ["dj_hyperview.W005"]
    assert "No SOURCES configured" in messages[0].msg


@override_settings(HYPERVIEW={"SOURCES": []})
def test_explicit_empty_sources_emit_an_actionable_warning() -> None:
    """An explicitly empty resolver stack reports its response consequence."""
    messages = check_hyperview_settings()

    assert [message.id for message in messages] == ["dj_hyperview.W005"]
    assert messages[0].hint == "Add a source or pass using='django'."


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"TEMPLATE_DIRS": "hyperview"}, ["E002"]),
        (
            {"TEMPLATE_DIRS": [object(), "/missing/dj-hyperview"]},
            ["E002", "W006"],
        ),
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

    assert [
        error.id.rsplit(".", 1)[-1]
        for error in errors
        if error.id != "dj_hyperview.W005"
    ] == expected
    assert all(error.hint and error.obj == "settings.HYPERVIEW" for error in errors)


def test_complete_valid_settings_pass_checks(tmp_path) -> None:
    schema = tmp_path / "schema.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" />',
        encoding="utf-8",
    )
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


def test_missing_filesystem_root_is_an_operational_warning(tmp_path) -> None:
    """A temporarily absent earlier root does not block a later mounted root."""
    missing = tmp_path / "unmounted"
    available = tmp_path / "available"
    available.mkdir()
    (available / "screen.xml").write_text("<view />", encoding="utf-8")
    configured = {
        "SOURCES": [
            {
                "BACKEND": "dj_hyperview.sources.filesystem.FileSystemSource",
                "OPTIONS": {"template_dirs": [missing, available]},
            }
        ]
    }

    with override_settings(HYPERVIEW=configured):
        messages = check_hyperview_settings()
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert [message.id for message in messages] == ["dj_hyperview.W006"]
    assert resolved.content == "<view />"


@override_settings(
    CACHES={"disabled": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    HYPERVIEW={"CACHE": {"ALIAS": "disabled"}},
)
def test_dummy_cache_emits_an_actionable_warning() -> None:
    """Configured DummyCache cannot satisfy Hyperview cache semantics."""
    messages = check_hyperview_settings()

    assert [message.id for message in messages] == ["dj_hyperview.W004"]
    assert messages[0].hint == (
        "Remove CACHE to disable Hyperview caching or configure a stateful backend."
    )


@pytest.mark.parametrize(
    "schema_content",
    [
        "<xs:schema>",
        (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            '<xs:include schemaLocation="core.xsd" />'
            "</xs:schema>"
        ),
        (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            '<xs:element name="view"><xs:complexType>'
            '<xs:assert test="true()" />'
            "</xs:complexType></xs:element></xs:schema>"
        ),
    ],
)
def test_schema_configuration_is_compiled_by_system_checks(
    tmp_path, schema_content
) -> None:
    """Invalid, composed, and unsupported schemas fail before first request."""
    schema = tmp_path / "screen.xsd"
    schema.write_text(schema_content, encoding="utf-8")

    with override_settings(HYPERVIEW={"VALIDATION": {"SCHEMA": schema}}):
        messages = check_hyperview_settings()

    assert [
        message.id for message in messages if message.id != "dj_hyperview.W005"
    ] == ["dj_hyperview.E008"]


def test_source_and_template_directory_mismatches_emit_warnings(tmp_path) -> None:
    """Checks expose inert roots and filesystem sources without roots."""
    filesystem = "dj_hyperview.sources.FileSystemSource"
    cases = [
        ({"TEMPLATE_DIRS": [tmp_path]}, ["W002"]),
        ({"SOURCES": [{"BACKEND": filesystem}]}, ["W003"]),
    ]

    for configured, expected in cases:
        with override_settings(HYPERVIEW=configured):
            messages = check_hyperview_settings()
        assert [message.id.rsplit(".", 1)[-1] for message in messages] == expected

    configured = {
        "SOURCES": [{"BACKEND": filesystem, "OPTIONS": {"template_dirs": [tmp_path]}}]
    }
    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []


def test_global_template_roots_are_probed_once_per_check(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consistency warnings reuse the already computed directory result."""
    calls: list[Path] = []
    original = Path.is_dir

    def tracked_is_dir(path: Path) -> bool:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(Path, "is_dir", tracked_is_dir)
    with override_settings(HYPERVIEW={"TEMPLATE_DIRS": [tmp_path]}):
        messages = check_hyperview_settings()

    assert [message.id for message in messages] == ["dj_hyperview.W002"]
    assert calls == [tmp_path]


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


@pytest.mark.parametrize(
    "extra_schemas",
    ["schema.xsd", ["https://example.test/app.xsd"], ["/missing/app.xsd"]],
)
def test_extra_schemas_must_be_a_sequence_of_local_files(extra_schemas) -> None:
    """Schema extensions fail startup checks before an editor or request uses them."""
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": extra_schemas}):
        messages = check_hyperview_settings()

    assert [
        message.id for message in messages if message.id != "dj_hyperview.W005"
    ] == ["dj_hyperview.E012"]


def test_valid_extra_schema_passes_startup_checks(tmp_path: Path) -> None:
    """A local XSD 1.1 extension is accepted when the schema extra is installed."""
    schema = tmp_path / "app.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="https://example.test/app"><xs:element name="item">'
        '<xs:complexType><xs:assert test="true()"/></xs:complexType>'
        "</xs:element></xs:schema>",
        encoding="utf-8",
    )

    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        assert [
            message
            for message in check_hyperview_settings()
            if message.id != "dj_hyperview.W005"
        ] == []


@override_settings(
    HYPERVIEW={"VALIDATION": {"SCHEMA": "dj_hyperview.validate_hyperview_schema"}}
)
def test_builtin_schema_validator_requires_the_schema_extra() -> None:
    """A configured optional validator reports its missing dependency at startup."""
    with patch("dj_hyperview.checks.find_spec", return_value=None):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E013"]
    assert "dj-hyperview[schema]" in relevant[0].hint


@pytest.mark.parametrize("admin_config", [True, [], {"EDITOR": "yes"}])
def test_admin_editor_configuration_requires_a_mapping_and_boolean(
    admin_config,
) -> None:
    """Malformed editor settings fail through stable startup checks."""
    with override_settings(HYPERVIEW={"ADMIN": admin_config}):
        messages = check_hyperview_settings()

    assert [
        message.id for message in messages if message.id != "dj_hyperview.W005"
    ] == ["dj_hyperview.E014"]


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_requires_django_ace_in_installed_apps() -> None:
    """Installing the extra alone does not silently omit django-ace setup."""
    messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E016"]
    assert "django_ace" in relevant[0].hint


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_reports_a_missing_optional_dependency() -> None:
    """Editor configuration explains which package extra must be installed."""
    with patch("dj_hyperview.checks.find_spec", return_value=None):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E015"]
    assert "dj-hyperview[editor]" in relevant[0].hint


@pytest.mark.parametrize(
    "permission",
    [object(), "missing.permission_callback", lambda: True],
)
def test_admin_permission_requires_a_single_request_callable(permission) -> None:
    """Invalid mutation policies fail through an actionable startup check."""
    with override_settings(HYPERVIEW={"ADMIN": {"PERMISSION": permission}}):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E017"]
    assert "request" in relevant[0].hint
