import sys
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import WARNING
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError
from dj_hyperview.resolver import TemplateResolver

if TYPE_CHECKING:
    from decimal import Decimal

FILESYSTEM = "dj_hyperview.sources.FileSystemSource"
DATABASE_SOURCE = "dj_hyperview.contrib.database.sources.DatabaseSource"
STUB_SOURCE = "tests.stubs.TemplateSource"


class RequiredOptionSource:
    """Source whose constructor requires one keyword option."""

    def __init__(self, *, token: str) -> None:
        self.token = token

    def resolve(self, name: str) -> None:
        return None


class QuotedAnnotationSource:
    """Source whose constructor names a type that only static checkers import."""

    def __init__(self, *, limit: "Decimal") -> None:
        self.limit = limit

    def resolve(self, name: str) -> None:
        return None


# Names a type that is never imported. Python 3.14 stores such an annotation without
# evaluating it, but earlier versions evaluate it when the definition runs, so the
# definitions are compiled only by the test that needs them.
LAZY_ANNOTATIONS = """
class LazyAnnotationSource:
    def __init__(self, *, token: NeverImported) -> None:
        self.token = token

    def resolve(self, name):
        return None


def lazy_annotation_permission(request: NeverImported) -> bool:
    return True
"""


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


XS = "http://www.w3.org/2001/XMLSchema"


@pytest.mark.parametrize(
    ("content", "cause"),
    [
        ("not xml", "schema_invalid"),
        (f'<schema xmlns="{XS}"><element/></schema>', "schema_invalid"),
        (f'<!DOCTYPE schema><schema xmlns="{XS}"/>', "forbidden_declaration"),
        (
            f'<schema xmlns="{XS}"><include schemaLocation="https://example.test/a.xsd"/>'
            "</schema>",
            "forbidden_schema_reference",
        ),
        (
            f'<schema xmlns="{XS}"><override schemaLocation="other.xsd"/></schema>',
            "forbidden_schema_reference",
        ),
        (
            f'<schema xmlns="{XS}"><include schemaLocation="//[::1/a.xsd"/></schema>',
            "forbidden_schema_reference",
        ),
    ],
)
def test_extra_schema_error_names_the_stable_cause(
    tmp_path: Path, content: str, cause: str
) -> None:
    """One message for every failure hid whether the file, a rule or the XSD failed."""
    schema = tmp_path / "app.xsd"
    schema.write_text(content, encoding="utf-8")
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        messages = [m for m in check_hyperview_settings() if m.id.endswith("E012")]

    assert [message.msg for message in messages] == [
        f"EXTRA_SCHEMAS[0] must be a valid local XSD 1.1 schema (reason: {cause})."
    ]
    assert str(tmp_path) not in messages[0].msg + messages[0].hint


@pytest.mark.parametrize(
    ("configured", "cause"), [("missing/app.xsd", "unreadable"), (7, "not_a_path")]
)
def test_unusable_extra_schema_entry_is_unreadable_or_not_a_path(
    tmp_path: Path, configured: object, cause: str
) -> None:
    entry = tmp_path / configured if isinstance(configured, str) else configured
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [entry]}):
        messages = [m for m in check_hyperview_settings() if m.id.endswith("E012")]

    assert [message.msg for message in messages] == [
        f"EXTRA_SCHEMAS[0] must be a valid local XSD 1.1 schema (reason: {cause})."
    ]
    assert str(tmp_path) not in messages[0].msg


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


REALTIME = {"REALTIME": {"REDIS_URL": "redis://localhost/0", "NAMESPACE": "app"}}


def _redis_spec(installed: bool):
    """Report only redis-py as present or missing; everything else is installed."""
    return lambda name: None if name == "redis" and not installed else object()


@override_settings(HYPERVIEW=REALTIME)
def test_configured_realtime_warns_when_redis_py_is_missing() -> None:
    """A missing extra is visible at startup, not only as a runtime log line."""
    with patch("dj_hyperview.checks.find_spec", side_effect=_redis_spec(False)):
        messages = check_hyperview_settings()

    relevant = [message for message in messages if message.id != "dj_hyperview.W005"]
    assert [message.id for message in relevant] == ["dj_hyperview.W007"]
    assert relevant[0].level == WARNING
    assert relevant[0].hint == 'pip install "dj-hyperview[realtime]"'
    assert "localhost" not in relevant[0].msg


@override_settings(HYPERVIEW=REALTIME)
def test_configured_realtime_is_silent_when_redis_py_is_installed() -> None:
    with patch("dj_hyperview.checks.find_spec", side_effect=_redis_spec(True)):
        messages = check_hyperview_settings()

    assert "dj_hyperview.W007" not in {message.id for message in messages}


@pytest.mark.parametrize("raw", [{}, {"REALTIME": None}])
def test_disabled_realtime_does_not_require_redis_py(raw) -> None:
    with (
        override_settings(HYPERVIEW=raw),
        patch("dj_hyperview.checks.find_spec", side_effect=_redis_spec(False)),
    ):
        messages = check_hyperview_settings()

    assert "dj_hyperview.W007" not in {message.id for message in messages}


@pytest.mark.parametrize(
    "make_stub",
    [lambda: ModuleType("stub"), MagicMock, SimpleNamespace],
    ids=["module", "magicmock", "namespace"],
)
@pytest.mark.parametrize(
    ("raw", "module", "absent"),
    [
        (REALTIME, "redis", "dj_hyperview.W007"),
        ({"ADMIN": {"EDITOR": True}}, "django_ace", "dj_hyperview.E015"),
        ({}, "xmlschema", "dj_hyperview.E013"),
    ],
)
def test_stub_module_without_a_spec_counts_as_installed(
    monkeypatch: pytest.MonkeyPatch, make_stub, raw, module: str, absent: str
) -> None:
    """A stand-in module in sys.modules makes find_spec raise, not return None.

    importlib reports a spec of None, or no spec at all, with ValueError. Such a
    module still imports, so the check treats it as installed rather than crashing.
    """
    monkeypatch.setitem(sys.modules, module, make_stub())
    with override_settings(HYPERVIEW=raw):
        messages = check_hyperview_settings()

    assert absent not in {message.id for message in messages}


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


@pytest.mark.parametrize(
    ("options", "expected"), [({"limit": 3}, []), ({"limt": 3}, ["E024"])]
)
def test_annotations_naming_checker_only_types_do_not_affect_the_options_check(
    options: dict, expected: list[str]
) -> None:
    """The constructor is bound without evaluating or resolving its annotations."""
    configured = {
        "SOURCES": [
            {"BACKEND": f"{__name__}.QuotedAnnotationSource", "OPTIONS": options}
        ]
    }
    with override_settings(HYPERVIEW=configured):
        assert _ids(check_hyperview_settings()) == expected


@pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="Python 3.14 evaluates annotations lazily, so a missing name fails on use",
)
def test_unresolved_annotation_names_neither_skip_e024_nor_crash_e017(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """signature() raises NameError for such a name unless it reads FORWARDREF."""
    definitions: dict[str, object] = {}
    exec(LAZY_ANNOTATIONS, definitions)
    for name in ("LazyAnnotationSource", "lazy_annotation_permission"):
        monkeypatch.setattr(
            sys.modules[__name__], name, definitions[name], raising=False
        )
    source = f"{__name__}.LazyAnnotationSource"

    def configured(options: dict, permission: str = "tests.stubs.allow_template_admin"):
        return {
            "SOURCES": [{"BACKEND": source, "OPTIONS": options}],
            "ADMIN": {"PERMISSION": permission},
        }

    with override_settings(HYPERVIEW=configured({"toke": 1})):
        assert _ids(check_hyperview_settings()) == ["E024"]
    with override_settings(HYPERVIEW=configured({"token": 1})):
        assert _ids(check_hyperview_settings()) == []
    permission = f"{__name__}.lazy_annotation_permission"
    with override_settings(HYPERVIEW=configured({"token": 1}, permission)):
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


@pytest.fixture
def exploding_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Return an importable module whose import raises with a secret in its message."""
    (tmp_path / "exploding_backend_module.py").write_text(
        'raise RuntimeError("secret-token")\n', encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "exploding_backend_module"


@pytest.mark.parametrize(
    ("backend", "cause"),
    [
        ("missing.Source", "ModuleNotFoundError"),
        (f"{STUB_SOURCE}Missing", "ImportError"),
        ("exploding_backend_module.Source", "RuntimeError"),
        (1, None),
    ],
)
def test_unimportable_backend_names_the_exception_class_only(
    exploding_module: str, backend: object, cause: str | None
) -> None:
    """The class name helps diagnosis; the message text can carry secrets or paths."""
    with override_settings(HYPERVIEW={"SOURCES": [{"BACKEND": backend}]}):
        messages = [m for m in check_hyperview_settings() if m.id.endswith("E003")]

    suffix = "" if cause is None else f" (import raised {cause})"
    assert [message.msg for message in messages] == [
        f"SOURCES[0].BACKEND must be importable{suffix}."
    ]
    assert "secret-token" not in messages[0].msg + messages[0].hint


@pytest.mark.parametrize(
    ("permission", "cause"),
    [
        ("missing.permission_callback", "ModuleNotFoundError"),
        ("exploding_backend_module.allow", "RuntimeError"),
        (object(), None),
    ],
)
def test_unimportable_admin_permission_names_the_exception_class_only(
    exploding_module: str, permission: object, cause: str | None
) -> None:
    with override_settings(HYPERVIEW={"ADMIN": {"PERMISSION": permission}}):
        messages = [m for m in check_hyperview_settings() if m.id.endswith("E017")]

    suffix = "" if cause is None else f" (import raised {cause})"
    assert [message.msg for message in messages] == [
        "ADMIN.PERMISSION must be a callable accepting request or a dotted path "
        f"to one{suffix}."
    ]
    assert "secret-token" not in messages[0].msg + messages[0].hint


@pytest.mark.parametrize("name", ["MAX_BYTES", "MAX_DEPTH", "MAX_NODES"])
def test_validation_limits_reject_int_subclasses_at_check_time(name: str) -> None:
    """Checks use the dataclass rule, so no bare ValueError escapes at runtime."""
    with override_settings(HYPERVIEW={"VALIDATION": {name: Limit(10)}}):
        assert _ids(check_hyperview_settings()) == ["E009"]
        with pytest.raises(HyperviewConfigurationError, match="E009"):
            get_settings()
