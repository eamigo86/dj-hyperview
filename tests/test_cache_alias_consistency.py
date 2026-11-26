"""Cache alias validation agrees between system checks and runtime."""

from collections.abc import Iterator
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


def cache_settings(*aliases: str) -> dict[str, dict[str, str]]:
    """Build in-memory cache settings for the given aliases.

    Args:
        *aliases: Cache aliases to configure.

    Returns:
        A CACHES mapping with one locmem backend per alias.
    """
    return {
        alias: {"BACKEND": BACKEND, "LOCATION": f"alias-consistency-{alias}"}
        for alias in aliases
    }


@contextmanager
def patched_alias(alias: str) -> Iterator[None]:
    """Patch the settings so that one alias is the only configured cache.

    Args:
        alias: Alias to configure and select for Hyperview.

    Yields:
        Control, while the patched settings are active.
    """
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
def test_configured_private_alias_passes_checks_and_round_trips() -> None:
    """A configured underscore alias passes checks and stores entries."""
    assert check_hyperview_settings() == []

    cache = TemplateCache.from_settings("tenant")
    cache.set_miss("memory", "screen.xml", "r1")

    assert cache.get("memory", "screen.xml", "r1") is CACHE_MISS


@pytest.mark.parametrize("alias", COLLISIONS)
def test_configured_handler_collisions_fail_checks_and_runtime(alias: str) -> None:
    """Aliases that collide with handler attributes fail checks and runtime.

    Args:
        alias: Handler attribute name used as the cache alias.
    """
    with patched_alias(alias):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable) as captured:
            TemplateCache("tenant", alias=alias)

    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"


def test_another_dunder_collision_is_discovered_and_rejected() -> None:
    """Another dunder alias that collides with the handler is found and rejected."""
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
def test_unconfigured_aliases_fail_checks_and_runtime_consistently(alias: str) -> None:
    """Unconfigured aliases are invalid in the checks and at runtime alike.

    Args:
        alias: Alias that is not configured in CACHES.
    """
    configured = cache_settings("default")
    with override_settings(CACHES=configured, HYPERVIEW={"CACHE": {"ALIAS": alias}}):
        errors = check_hyperview_settings()
        with pytest.raises(SourceUnavailable) as captured:
            TemplateCache("tenant", alias=alias)

    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"


@pytest.mark.parametrize("alias", ["", None, True, [], {}])
def test_empty_and_non_string_aliases_remain_invalid(alias: object) -> None:
    """Empty and non-string aliases are invalid in the checks and at runtime.

    Args:
        alias: Invalid alias value.
    """
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
def test_backend_initialization_failure_is_safe_in_checks_and_runtime() -> None:
    """A backend that fails to start yields a warning and a safe runtime error."""
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
def test_system_check_resolves_backend_without_cache_io() -> None:
    """The system check resolves the backend without calling the cache."""
    assert check_hyperview_settings() == []


@pytest.mark.parametrize("field", ["namespace", "source", "name", "revision"])
def test_cache_key_supports_lone_surrogates_without_escape_collisions(
    field: str,
) -> None:
    """A lone surrogate gives a stable key, distinct from its escaped text.

    Args:
        field: Key component that receives the lone surrogate.
    """
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
