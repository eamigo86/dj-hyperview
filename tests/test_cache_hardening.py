import json

import pytest
from django.core.cache import caches
from django.test import override_settings

from dj_hyperview.cache import CACHE_MISS, TemplateCache
from dj_hyperview.exceptions import SourceUnavailable

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cache-hardening",
    },
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cache-hardening-screens",
    },
}
BROKEN_CACHES = LOCMEM_CACHES | {
    "broken": {"BACKEND": "tests.stubs.MissingCacheBackend"}
}
LOOKUP = ("memory", "screen.xml", "r1")


@pytest.fixture(autouse=True)
def configured_cache():
    with override_settings(CACHES=LOCMEM_CACHES):
        yield


def template_envelope(**updates):
    template = {
        "name": "screen.xml",
        "content": "<view />",
        "origin": "memory:screen.xml",
        "source": "memory",
        "revision": "r1",
    }
    template.update(updates)
    return {"version": 1, "state": "template", "template": template}


def store_payload(cache, payload):
    caches["screens"].set(cache.key(*LOOKUP), json.dumps(payload))


def store_raw(cache, payload):
    caches["screens"].set(cache.key(*LOOKUP), payload)


@pytest.mark.parametrize("state", ["miss", "template"])
@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2, None, [], {}])
def test_cache_rejects_non_integer_envelope_versions(state, version):
    cache = TemplateCache(f"version-{state}-{version}", alias="screens")
    payload = (
        {"version": version, "state": "miss"}
        if state == "miss"
        else template_envelope() | {"version": version}
    )
    store_payload(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


def test_cache_rejects_unexpected_template_envelope_fields():
    cache = TemplateCache("outer-fields", alias="screens")
    store_payload(cache, template_envelope() | {"extra": "untrusted"})

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("source", "other"), ("name", "other.xml"), ("revision", "r9")],
)
def test_cache_rejects_template_identity_substitution(field, replacement):
    cache = TemplateCache(f"identity-{field}", alias="screens")
    store_payload(cache, template_envelope(**{field: replacement}))

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "miss"},
        {"version": 1},
        {"version": 1, "state": "miss", "extra": None},
        {"version": 1, "state": "template"},
        template_envelope() | {"extra": None},
    ],
)
def test_cache_rejects_missing_or_extra_envelope_fields(payload):
    cache = TemplateCache(
        f"shape-{len(payload)}-{payload.get('state')}", alias="screens"
    )
    store_payload(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    "payload",
    [
        '{"version":1,"version":1,"state":"miss"}',
        (
            '{"version":1,"state":"template","template":{'
            '"name":"screen.xml","name":"screen.xml","content":"<view />",'
            '"origin":"memory:screen.xml","source":"memory","revision":"r1"}}'
        ),
    ],
)
def test_cache_rejects_duplicate_json_object_keys(payload):
    cache = TemplateCache(f"duplicate-{len(payload)}", alias="screens")
    store_raw(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("name", None),
        ("content", ["<view />"]),
        ("origin", {"path": "memory:screen.xml"}),
        ("source", True),
        ("revision", 1),
    ],
)
def test_cache_rejects_non_string_template_fields(field, replacement):
    cache = TemplateCache(f"type-{field}", alias="screens")
    store_payload(cache, template_envelope(**{field: replacement}))

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        None,
        "unexpected",
        True,
        1,
        {"version": 1, "state": "future"},
    ],
)
def test_cache_rejects_unexpected_json_values_and_states(payload):
    cache = TemplateCache(f"json-{type(payload).__name__}", alias="screens")
    store_payload(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"version":1,"state":"miss"}',
        bytearray(b'{"version":1,"state":"miss"}'),
        123,
    ],
)
def test_cache_rejects_non_string_backend_payloads(payload):
    cache = TemplateCache(f"backend-{type(payload).__name__}", alias="screens")
    store_raw(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize("alias", [None, True, "", [], {}, ()])
def test_cache_normalizes_invalid_alias_types(alias):
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias=alias)

    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"
    assert str(captured.value) == "Template source unavailable: cache (invalid alias)"


@override_settings(CACHES=BROKEN_CACHES)
def test_cache_normalizes_invalid_backend_configuration_without_leaking_cause():
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias="broken")

    assert captured.value.source == "cache:broken"
    assert captured.value.reason == "backend failure"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("kwargs", [{"ttl": True}, {"negative_ttl": True}])
def test_cache_rejects_boolean_timeouts(kwargs):
    with pytest.raises(ValueError, match="must be an integer"):
        TemplateCache("tenant", **kwargs)


def test_cache_still_accepts_the_exact_supported_miss_envelope():
    cache = TemplateCache("exact-miss", alias="screens")
    store_payload(
        cache,
        {
            "version": 1,
            "state": "miss",
            "source": "memory",
            "name": "screen.xml",
            "revision": "r1",
        },
    )

    assert cache.get(*LOOKUP) is CACHE_MISS
