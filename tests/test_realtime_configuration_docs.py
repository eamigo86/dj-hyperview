"""Execute the complete settings example and its non-destructive SSE adoption."""

import builtins
import re
import socket
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings

from dj_hyperview import TemplateResolver, validate_hyperview_schema
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "docs/configuration.md"
GUIDE = ROOT / "docs/realtime.md"
KEYS = {
    "TEMPLATE_DIRS",
    "SOURCES",
    "CACHE",
    "VALIDATION",
    "ADMIN",
    "EXTRA_SCHEMAS",
    "SCHEMA_EXTENSIONS",
    "REALTIME",
}


def first_example():
    return re.search(r"```python\n(.*?)\n```", CONFIG.read_text(), re.DOTALL)[1]


def test_complete_example_immediately_follows_title():
    assert re.match(r"^# [^\n]+\n\n```python\n", CONFIG.read_text())


@pytest.fixture
def documented_settings(tmp_path):
    # Only declared prerequisites: a project-owned template directory and a
    # configured default cache. No Redis service or extra schema file is needed.
    (tmp_path / "hyperview").mkdir()
    (tmp_path / "hyperview/home.xml").write_text(
        '<doc xmlns="https://hyperview.org/hyperview"><screen><body /></screen></doc>'
    )
    namespace = {"__file__": str(tmp_path / "config/settings.py")}
    exec(compile(first_example(), str(CONFIG), "exec"), namespace)
    return namespace["HYPERVIEW"]


def test_first_example_covers_all_public_sections_and_source_options(
    documented_settings,
):
    raw = documented_settings
    assert set(raw) == KEYS
    assert set(raw["SOURCES"][0]) == {"BACKEND", "OPTIONS"}
    assert set(raw["CACHE"]) == {
        "ALIAS",
        "NAMESPACE",
        "TTL",
        "NEGATIVE_TTL",
        "FAILURE_MODE",
    }
    assert set(raw["VALIDATION"]) == {"MAX_BYTES", "MAX_DEPTH", "MAX_NODES"}
    assert set(raw["ADMIN"]) == {"EDITOR", "PERMISSION"}
    assert raw["ADMIN"]["EDITOR"] is False
    assert raw["EXTRA_SCHEMAS"] == []
    assert set(raw["REALTIME"]) == {"REDIS_URL", "NAMESPACE"}
    assert raw["SCHEMA_EXTENSIONS"]["BEHAVIORS"]["show-snackbar"]["ATTRIBUTES"][
        "message"
    ] == {"TYPE": "string", "REQUIRED": True}


def test_complete_example_passes_actual_checks_without_redis_import_or_network(
    documented_settings, monkeypatch
):
    original = builtins.__import__

    def import_without_redis(name, *args, **kwargs):
        assert name != "redis" and not name.startswith("redis."), name
        return original(name, *args, **kwargs)

    def no_connection(*args, **kwargs):
        pytest.fail("A documented configuration must not connect to Redis")

    monkeypatch.setattr(builtins, "__import__", import_without_redis)
    monkeypatch.setattr(socket, "create_connection", no_connection)
    with override_settings(
        HYPERVIEW=documented_settings,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "complete-doc-config",
            }
        },
    ):
        assert check_hyperview_settings() == []
        current = get_settings()
        assert (
            current.realtime.redis_url == documented_settings["REALTIME"]["REDIS_URL"]
        )
        assert (
            current.realtime.namespace == documented_settings["REALTIME"]["NAMESPACE"]
        )
        with pytest.raises(FrozenInstanceError):
            current.realtime.namespace = "changed"
        assert current.realtime.redis_url not in repr(current.realtime)
        assert TemplateResolver.from_settings().resolve("home.xml") is not None
        request = RequestFactory().get("/")
        for admin in (True, False):
            request.user = SimpleNamespace(is_superuser=admin)
            assert current.admin.permission(request) is admin


def test_example_schema_descriptors_enforce_required_and_enum(documented_settings):
    with override_settings(HYPERVIEW=documented_settings):
        prefix = '<view xmlns="https://hyperview.org/hyperview">'
        validate_hyperview_schema(
            prefix + '<behavior trigger="load" action="show-snackbar" '
            'message="Saved" tone="success" /></view>'
        )
        for attributes in ('tone="success"', 'message="Saved" tone="invented"'):
            with pytest.raises(TemplateValidationError):
                validate_hyperview_schema(
                    prefix
                    + '<behavior trigger="load" action="show-snackbar" '
                    + attributes
                    + " /></view>"
                )


@pytest.mark.parametrize("realtime", [None, "omitted"])
def test_documented_disabled_realtime_keeps_sources_and_cache(
    documented_settings, realtime
):
    raw = {**documented_settings, "REALTIME": realtime}
    if realtime == "omitted":
        raw.pop("REALTIME")
    with override_settings(HYPERVIEW=raw):
        assert get_settings().realtime is None
        assert get_settings().sources
        assert get_settings().cache.alias == "default"


def test_invalid_realtime_never_falls_back_to_disabled(documented_settings):
    raw = {**documented_settings, "REALTIME": {"ALIAS": "default"}}
    with override_settings(HYPERVIEW=raw):
        assert any(
            item.id == "dj_hyperview.E022" for item in check_hyperview_settings()
        )
        with pytest.raises(HyperviewConfigurationError):
            get_settings()


def test_guide_adoption_merges_existing_hyperview_without_losing_extensions(
    documented_settings, monkeypatch
):
    monkeypatch.setenv("APP_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("APP_REALTIME_NAMESPACE", "docs-merge")
    match = re.search(
        r"<!-- example: realtime-settings -->\s*```python\n(.*?)\n```",
        GUIDE.read_text(),
        re.DOTALL,
    )
    assert match, "The guide must expose its executable REALTIME adoption example"
    # Execute the actual relative import against a declared synthetic base module.
    import sys
    from types import ModuleType

    base = ModuleType("docs_config.settings")
    base.HYPERVIEW = documented_settings
    monkeypatch.setitem(sys.modules, "docs_config.settings", base)
    namespace = {"__package__": "docs_config"}
    exec(compile(match[1], str(GUIDE), "exec"), namespace)
    raw = namespace["HYPERVIEW"]
    assert raw is not documented_settings
    assert set(raw) == KEYS
    for name in KEYS - {"REALTIME"}:
        assert raw[name] is documented_settings[name]
    assert raw["REALTIME"] == {
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "NAMESPACE": "docs-merge",
    }
    assert namespace["SESSION_ENGINE"] == "django.contrib.sessions.backends.db"
    with override_settings(HYPERVIEW=raw):
        assert get_settings().realtime.namespace == "docs-merge"


def test_public_references_point_to_central_realtime_configuration():
    for name in (
        "configuration.md",
        "realtime.md",
        "installation.md",
        "api-reference.md",
        "security.md",
        "changelog.md",
    ):
        assert "REALTIME" in (ROOT / "docs" / name).read_text(), name
    assert "There is no new `HYPERVIEW` setting" not in CONFIG.read_text()
    assert "get_settings().realtime" in (ROOT / "docs/api-reference.md").read_text()


def test_configuration_sections_preserve_existing_incoming_anchors():
    source = CONFIG.read_text()
    # English headings retain their original generated anchors; explicit IDs
    # remain supported without requiring duplicate IDs on the rendered page.
    anchors = {
        re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
        for title in re.findall(r"^#+ (.+)$", source, re.MULTILINE)
    } | set(re.findall(r'<a id="([^"]+)">', source))
    for page in (ROOT / "docs").glob("*.md"):
        for anchor in re.findall(r"\]\(configuration\.md#([^)]+)\)", page.read_text()):
            assert anchor in anchors, (page.name, anchor)
