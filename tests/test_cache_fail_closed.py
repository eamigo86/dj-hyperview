import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import BaseCache
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.sources import ResolvedTemplate

SECRET = "<view>consumer-secret</view>"
LOOKUP = ("memory", "screen.xml", "r1")


class ExplodingCache(BaseCache):
    def __init__(self, location, params):
        super().__init__(params)
        self.operation = params["OPTIONS"]["OPERATION"]

    def get(self, key, default=None, version=None):
        del key, default, version
        if self.operation == "get":
            raise RuntimeError(SECRET)
        return None

    def set(self, key, value, timeout=None, version=None):
        del key, timeout, version
        if self.operation == "set":
            raise RuntimeError(f"failed to store {value}")
        return True


class ExplodingInitCache(BaseCache):
    def __init__(self, location, params):
        del location, params
        raise RuntimeError(SECRET)


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fail-closed-default",
    },
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fail-closed-screens",
    },
    "tenant cache/ñ": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fail-closed-unusual",
    },
    "failure-get": {
        "BACKEND": "tests.test_cache_fail_closed.ExplodingCache",
        "OPTIONS": {"OPERATION": "get"},
    },
    "failure-set": {
        "BACKEND": "tests.test_cache_fail_closed.ExplodingCache",
        "OPTIONS": {"OPERATION": "set"},
    },
    "failure-init": {
        "BACKEND": "tests.test_cache_fail_closed.ExplodingInitCache",
    },
}


@pytest.fixture(autouse=True)
def configured_caches():
    with override_settings(CACHES=CACHES):
        yield


def assert_safe_backend_error(error, alias):
    assert error.source == f"cache:{alias}"
    assert error.reason == "backend failure"
    assert str(error) == f"Template source unavailable: cache:{alias} (backend failure)"
    assert SECRET not in str(error)
    assert SECRET not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_cached_miss_is_bound_to_source_name_and_revision():
    cache = TemplateCache("tenant", alias="screens")
    cache.set_miss("other", "other.xml", "r9")
    payload = caches["screens"].get(cache.key("other", "other.xml", "r9"))
    caches["screens"].set(cache.key(*LOOKUP), payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


def miss_envelope(**updates):
    value = {
        "version": 1,
        "state": "miss",
        "source": "memory",
        "name": "screen.xml",
        "revision": "r1",
    }
    value.update(updates)
    return value


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("source", "other"), ("name", "other.xml"), ("revision", "r9")],
)
def test_cached_miss_rejects_each_identity_substitution(field, replacement):
    cache = TemplateCache(f"identity-{field}", alias="screens")
    caches["screens"].set(
        cache.key(*LOOKUP),
        json.dumps(miss_envelope(**{field: replacement})),
    )

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "state": "miss", "source": "memory", "name": "screen.xml"},
        miss_envelope(extra="value"),
        miss_envelope(source=None),
        miss_envelope(name=["screen.xml"]),
        miss_envelope(revision=None),
    ],
)
def test_cached_miss_rejects_inexact_shapes_and_field_types(payload):
    cache = TemplateCache(f"shape-{repr(payload)}", alias="screens")
    caches["screens"].set(cache.key(*LOOKUP), json.dumps(payload))

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


def test_cached_miss_rejects_duplicate_identity_fields():
    cache = TemplateCache("duplicate-miss", alias="screens")
    payload = (
        '{"version":1,"state":"miss","source":"memory","source":"memory",'
        '"name":"screen.xml","revision":"r1"}'
    )
    caches["screens"].set(cache.key(*LOOKUP), payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


def test_cached_miss_round_trips_unicode_and_literal_none_revision():
    lookup = ("memoria-ñ", "pantalla/🦊.xml", "None")
    cache = TemplateCache("unicode", alias="tenant cache/ñ")

    cache.set_miss(*lookup)

    assert cache.get(*lookup).is_miss is True


@pytest.mark.parametrize("operation", ["get", "set", "set_miss"])
def test_runtime_backend_failures_are_safe_and_deterministic(operation):
    alias = "failure-get" if operation == "get" else "failure-set"
    cache = TemplateCache("tenant", alias=alias)

    with pytest.raises(SourceUnavailable) as captured:
        if operation == "get":
            cache.get(*LOOKUP)
        elif operation == "set":
            cache.set(
                ResolvedTemplate("screen.xml", SECRET, "secret-origin", "memory", "r1")
            )
        else:
            cache.set_miss(*LOOKUP)

    assert_safe_backend_error(captured.value, alias)


def test_backend_initialization_failure_is_safe_and_deterministic():
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias="failure-init")

    assert_safe_backend_error(captured.value, "failure-init")


def test_corrupt_payload_failure_does_not_leak_payload_or_exception_chain():
    cache = TemplateCache("corrupt", alias="screens")
    caches["screens"].set(cache.key(*LOOKUP), SECRET)

    with pytest.raises(SourceUnavailable) as captured:
        cache.get(*LOOKUP)

    error = captured.value
    assert error.reason == "invalid payload"
    assert SECRET not in str(error)
    assert SECRET not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("alias", ["__dict__", "__class__", "_storage", "missing"])
def test_unsafe_and_unconfigured_aliases_fail_before_cache_handler_access(alias):
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias=alias)

    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("alias", ["__dict__", "__class__", "_storage"])
def test_unsafe_aliases_are_rejected_even_when_configured(alias):
    configured = CACHES | {
        alias: {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"fail-closed-{alias}",
        }
    }

    with patch.object(settings, "CACHES", configured):
        with pytest.raises(SourceUnavailable, match="invalid alias"):
            TemplateCache("tenant", alias=alias)
