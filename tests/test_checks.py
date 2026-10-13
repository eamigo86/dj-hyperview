from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError
from dj_hyperview.resolver import TemplateResolver

FILESYSTEM = "dj_hyperview.sources.FileSystemSource"
DATABASE_SOURCE = "dj_hyperview.contrib.database.sources.DatabaseSource"
STUB_SOURCE = "tests.stubs.TemplateSource"


class RequiredOptionSource:
    """Source whose constructor requires one keyword option."""

    def __init__(self, *, token: str) -> None:
        self.token = token

    def resolve(self, name: str) -> None:
        return None


class Limit(int):
    """An int subclass that the typed settings reject."""


class OpaqueBackend:
    """Callable backend whose signature cannot be introspected."""

    @property
    def __signature__(self):
        raise RuntimeError("signature unavailable")

    def __call__(self, **options: object) -> object:
        return object()


OPAQUE_BACKEND = OpaqueBackend()


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
        "EXTRA_SCHEMAS": [schema],
    }
    caches = {"screens": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
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
def test_removed_schema_replacement_is_rejected_before_reading(
    tmp_path, schema_content
) -> None:
    """Retired schema replacement paths never select another compiler."""
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


@override_settings(HYPERVIEW={})
def test_builtin_validator_requires_its_regular_dependency() -> None:
    """Every installation reports a missing mandatory dependency at startup."""
    with patch("dj_hyperview.checks.find_spec", return_value=None):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E013"]
    assert "required dependency" in relevant[0].msg


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
    with patch(
        "dj_hyperview.checks.find_spec",
        side_effect=lambda name: None if name == "django_ace" else object(),
    ):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E015"]
    assert "dj-hyperview[editor]" in relevant[0].hint


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_editor_requires_schema_dependency_for_static_validation() -> None:
    """The editor fails startup when its static XSD dependency is incomplete."""

    def dependency(name: str):
        return None if name == "xmlschema" else object()

    with (
        patch("dj_hyperview.checks.find_spec", side_effect=dependency),
        patch("dj_hyperview.checks.apps.is_installed", return_value=True),
    ):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.E013"]
    assert "required dependency" in relevant[0].msg


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


def _ids(messages) -> list[str]:
    """Return short check ids, ignoring the unrelated empty-sources warning."""
    return [
        message.id.rsplit(".", 1)[-1]
        for message in messages
        if message.id != "dj_hyperview.W005"
    ]


@pytest.mark.parametrize(
    ("configured", "unknown", "valid"),
    [
        ({"TEMPLATE_DIR": []}, "HYPERVIEW.TEMPLATE_DIR", "TEMPLATE_DIRS"),
        ({"cache": {"TTL": 5}}, "HYPERVIEW.cache", "CACHE"),
        ({"CACHE": {"TTLL": 5}}, "CACHE.TTLL", "NEGATIVE_TTL"),
        ({"VALIDATION": {"MAX_BYTE": 10}}, "VALIDATION.MAX_BYTE", "MAX_BYTES"),
        ({"ADMIN": {"PERMISION": "x.y"}}, "ADMIN.PERMISION", "PERMISSION"),
        (
            {"SOURCES": [{"BACKEND": STUB_SOURCE, "OPTION": {"content": "x"}}]},
            "SOURCES[0].OPTION",
            "OPTIONS",
        ),
    ],
)
def test_unknown_keys_are_configuration_errors(configured, unknown, valid) -> None:
    """A typo in any settings section can no longer fall back to a default."""
    with override_settings(HYPERVIEW=configured):
        messages = check_hyperview_settings()

    assert _ids(messages) == ["E023"]
    assert unknown in messages[0].msg
    assert valid in messages[0].hint
    assert messages[0].obj == "settings.HYPERVIEW"


def test_typos_fail_closed_instead_of_using_defaults() -> None:
    """The reported typo set is rejected, not silently replaced by defaults."""
    configured = {
        "VALIDATION": {"MAX_BYTE": 10},
        "ADMIN": {"PERMISION": "no.such.fn"},
        "TEMPLATE_DIR": ["/srv/hyperview"],
    }
    with override_settings(HYPERVIEW=configured):
        assert _ids(check_hyperview_settings()) == ["E023"] * 3
        with pytest.raises(HyperviewConfigurationError, match="E023") as captured:
            get_settings()

    assert len(captured.value.issues) == 3


def test_retired_keys_keep_their_dedicated_errors_without_e023() -> None:
    """Removed settings keep E007, E008 and E019 and never double-report."""
    configured = {
        "SCHEMA_PROFILE": "legacy",
        "VALIDATION": {"MODE": "off", "SCHEMA": "app.xsd", "MAX_BYTE": 1},
    }
    with override_settings(HYPERVIEW=configured):
        messages = check_hyperview_settings()

    assert Counter(_ids(messages)) == {"E007": 1, "E008": 1, "E019": 1, "E023": 1}
    unknown = next(message for message in messages if message.id.endswith("E023"))
    assert "VALIDATION.MAX_BYTE" in unknown.msg
    assert "MODE" not in unknown.msg and "SCHEMA" not in unknown.msg


def test_every_documented_key_is_accepted(tmp_path: Path) -> None:
    """The unknown-key rule rejects only keys that no section reads."""
    configured = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": FILESYSTEM, "OPTIONS": {"template_dirs": [tmp_path]}}],
        "CACHE": {
            "ALIAS": "default",
            "NAMESPACE": "documented-keys",
            "TTL": 300,
            "NEGATIVE_TTL": 15,
            "FAILURE_MODE": "bypass",
        },
        "VALIDATION": {"MAX_BYTES": 1_000_000, "MAX_DEPTH": 64, "MAX_NODES": 20_000},
        "ADMIN": {"EDITOR": False, "PERMISSION": "tests.stubs.allow_template_admin"},
        "EXTRA_SCHEMAS": [],
        "SCHEMA_EXTENSIONS": {},
        "REALTIME": None,
    }
    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []
        assert get_settings().validation.max_bytes == 1_000_000


@pytest.mark.parametrize(
    ("backend", "options", "expected"),
    [
        (FILESYSTEM, {"template_dir": ["/srv/hyperview"]}, ["E024"]),
        (f"{__name__}.RequiredOptionSource", {}, ["E024"]),
        (f"{__name__}.RequiredOptionSource", {"token": "x", "extra": 1}, ["E024"]),
        (f"{__name__}.RequiredOptionSource", {1: "x"}, ["E024"]),
        (f"{__name__}.RequiredOptionSource", {"token": "x"}, []),
        (STUB_SOURCE, {"x": 1}, ["E024"]),
        (STUB_SOURCE, {"content": "x", "revision": "r1"}, []),
        (DATABASE_SOURCE, {"alias": "default"}, ["E010", "E024"]),
    ],
)
def test_source_options_must_match_the_backend_constructor(
    tmp_path: Path, backend: str, options: dict, expected: list[str]
) -> None:
    """A mistyped constructor option fails the check instead of every request."""
    configured: dict = {"SOURCES": [{"BACKEND": backend, "OPTIONS": options}]}
    if backend == FILESYSTEM:
        configured["TEMPLATE_DIRS"] = [tmp_path]
    with override_settings(HYPERVIEW=configured):
        messages = check_hyperview_settings()

    assert _ids(messages) == expected
    for message in messages:
        if message.id.endswith("E024"):
            assert message.msg == (
                "SOURCES[0].OPTIONS do not match the backend constructor."
            )
            assert message.hint and message.obj == "settings.HYPERVIEW"


def test_signature_introspection_failure_never_breaks_or_fails_the_check() -> None:
    """A backend that cannot be inspected is left to runtime construction."""
    configured = {
        "SOURCES": [{"BACKEND": f"{__name__}.OPAQUE_BACKEND", "OPTIONS": {"x": 1}}]
    }
    with override_settings(HYPERVIEW=configured):
        assert _ids(check_hyperview_settings()) == []


def test_mismatched_options_fail_settings_instead_of_each_request(tmp_path) -> None:
    """Resolver construction reports the typo as a configuration error."""
    configured = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": FILESYSTEM, "OPTIONS": {"template_dir": [tmp_path]}}],
    }
    with override_settings(HYPERVIEW=configured):
        with pytest.raises(HyperviewConfigurationError, match="E024"):
            TemplateResolver.from_settings()


@pytest.mark.parametrize(
    "configured",
    [
        {"SOURCES": [{"BACKEND": "missing.Source", "OPTIONS": {"x": 1}}]},
        {"SOURCES": [{"BACKEND": STUB_SOURCE, "OPTIONS": []}]},
        {"SOURCES": [{"BACKEND": FILESYSTEM, "OPTIONS": "template_dirs"}]},
    ],
)
def test_unusable_backend_or_options_report_only_their_own_error(configured) -> None:
    """E024 needs an importable backend and a mapping of options."""
    with override_settings(HYPERVIEW=configured):
        assert set(_ids(check_hyperview_settings())) == {"E003"}


@pytest.mark.parametrize("name", ["MAX_BYTES", "MAX_DEPTH", "MAX_NODES"])
def test_validation_limits_reject_int_subclasses_at_check_time(name: str) -> None:
    """Checks use the dataclass rule, so no bare ValueError escapes at runtime."""
    with override_settings(HYPERVIEW={"VALIDATION": {name: Limit(10)}}):
        assert _ids(check_hyperview_settings()) == ["E009"]
        with pytest.raises(HyperviewConfigurationError, match="E009"):
            get_settings()
