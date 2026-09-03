from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import BaseCache
from django.test import override_settings

from dj_hyperview.cache import CACHE_MISS, TemplateCache, template_cache_key
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.exceptions import SourceUnavailable

BACKEND = "django.core.cache.backends.locmem.LocMemCache"
COLLISIONS = ("__dict__", "__class__", "_storage")


def cache_settings(*aliases):
    return {
        alias: {"BACKEND": BACKEND, "LOCATION": f"alias-consistency-{alias}"}
        for alias in aliases
    }


@contextmanager
def patched_alias(alias):
    configured = cache_settings(alias)
    hyperview = {"CACHE": {"ALIAS": alias}}
    with (
        patch.object(settings, "CACHES", configured),
        patch.object(settings, "HYPERVIEW", hyperview, create=True),
        patch.object(caches, "settings", configured),
    ):
        yield


@override_settings(
    CACHES=cache_settings("_private"),
    HYPERVIEW={"CACHE": {"ALIAS": "_private"}},
)
def test_configured_private_alias_passes_checks_and_round_trips():
    assert check_hyperview_settings() == []

    cache = TemplateCache.from_settings("tenant")
    cache.set_miss("memory", "screen.xml", "r1")

    assert cache.get("memory", "screen.xml", "r1") is CACHE_MISS


@pytest.mark.parametrize("alias", COLLISIONS)
def test_configured_handler_collisions_fail_checks_and_runtime(alias):
    with patched_alias(alias):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable) as captured:
            TemplateCache("tenant", alias=alias)

    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"


def test_another_dunder_collision_is_discovered_and_rejected():
    candidates = sorted(set(dir(object)) - set(COLLISIONS))
    configured = cache_settings(*candidates)
    with patch.object(caches, "settings", configured):
        alias = next(
            candidate
            for candidate in candidates
            if not isinstance(caches[candidate], BaseCache)
        )

    with patched_alias(alias):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable, match="invalid alias"):
            TemplateCache("tenant", alias=alias)

    assert alias.startswith("__")
    assert [error.id for error in errors] == ["dj_hyperview.E004"]


@pytest.mark.parametrize("alias", ["_private", *COLLISIONS])
def test_unconfigured_aliases_fail_checks_and_runtime_consistently(alias):
    configured = cache_settings("default")
    with override_settings(CACHES=configured, HYPERVIEW={"CACHE": {"ALIAS": alias}}):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable) as captured:
            TemplateCache("tenant", alias=alias)

    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"


@pytest.mark.parametrize("alias", ["", None, True, [], {}])
def test_empty_and_non_string_aliases_remain_invalid(alias):
    with override_settings(HYPERVIEW={"CACHE": {"ALIAS": alias}}):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable) as captured:
            TemplateCache("tenant", alias=alias)

    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert captured.value.reason == "invalid alias"


@override_settings(
    CACHES={
        "broken": {
            "BACKEND": "tests.test_cache_fail_closed.ExplodingInitCache",
        }
    },
    HYPERVIEW={"CACHE": {"ALIAS": "broken"}},
)
def test_backend_initialization_failure_is_safe_in_checks_and_runtime():
    errors = check_hyperview_settings()
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias="broken")

    assert [error.id for error in errors] == ["dj_hyperview.W001"]
    assert errors[0].msg == "CACHE.ALIAS references an unavailable backend."
    assert errors[0].hint == (
        "Cache failure behavior follows CACHE.FAILURE_MODE at runtime."
    )
    assert captured.value.source == "cache:broken"
    assert captured.value.reason == "backend failure"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@override_settings(
    CACHES={
        "checked": {
            "BACKEND": "tests.test_cache_fail_closed.ExplodingCache",
            "OPTIONS": {"OPERATION": "get"},
        }
    },
    HYPERVIEW={"CACHE": {"ALIAS": "checked"}},
)
def test_system_check_resolves_backend_without_cache_io():
    assert check_hyperview_settings() == []


@pytest.mark.parametrize("field", ["namespace", "source", "name", "revision"])
def test_cache_key_supports_lone_surrogates_without_escape_collisions(field):
    surrogate = "\ud800"
    components = {
        "namespace": "tenant",
        "source": "memory",
        "name": "screen.xml",
        "revision": "r1",
    }
    first = template_cache_key(**(components | {field: surrogate}))

    assert first == template_cache_key(**(components | {field: surrogate}))
    assert first != template_cache_key(**(components | {field: r"\ud800"}))
    assert first.isascii()
    assert len(first) == 72
