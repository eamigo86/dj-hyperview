"""Cache payload validation rejects every shape except the exact envelope."""

import json
from collections.abc import Iterator

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
def configured_cache() -> Iterator[None]:
    """Configure the in-memory caches of this module for every test.

    Yields:
        Control, while the cache settings are overridden.
    """
    with override_settings(CACHES=LOCMEM_CACHES):
        yield


def template_envelope(**updates: str) -> dict[str, object]:
    """Build a valid template payload with some template fields replaced.

    Args:
        **updates: Template fields that replace the valid ones.

    Returns:
        The template payload as a dictionary.
    """
    template = {
        "name": "screen.xml",
        "content": "<view />",
        "origin": "memory:screen.xml",
        "source": "memory",
        "revision": "r1",
    }
    template.update(updates)
    return {"version": 1, "state": "template", "template": template}


def store_payload(cache: TemplateCache, payload: object) -> None:
    """Store a payload as JSON under the lookup key.

    Args:
        cache: Cache that builds the key.
        payload: Value serialized to JSON and stored.
    """
    caches["screens"].set(cache.key(*LOOKUP), json.dumps(payload))


def store_raw(cache: TemplateCache, payload: object) -> None:
    """Store a payload as is under the lookup key.

    Args:
        cache: Cache that builds the key.
        payload: Value stored without serialization.
    """
    caches["screens"].set(cache.key(*LOOKUP), payload)


@pytest.mark.parametrize("state", ["miss", "template"])
@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2, None, [], {}])
def test_cache_rejects_non_integer_envelope_versions(
    state: str, version: object
) -> None:
    """Only the integer version 1 is accepted in an envelope.

    Args:
        state: Envelope state, miss or template.
        version: Version value that is stored.
    """
    cache = TemplateCache(f"version-{state}-{version}", alias="screens")
    payload = (
        {"version": version, "state": "miss"}
        if state == "miss"
        else template_envelope() | {"version": version}
    )
    store_payload(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


def test_cache_rejects_unexpected_template_envelope_fields() -> None:
    """A template envelope with an extra field is rejected."""
    cache = TemplateCache("outer-fields", alias="screens")
    store_payload(cache, template_envelope() | {"extra": "untrusted"})

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("source", "other"), ("name", "other.xml"), ("revision", "r9")],
)
def test_cache_rejects_template_identity_substitution(
    field: str, replacement: str
) -> None:
    """A template whose identity differs from the lookup is rejected.

    Args:
        field: Identity field that is replaced.
        replacement: Value that replaces the field.
    """
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
def test_cache_rejects_missing_or_extra_envelope_fields(
    payload: dict[str, object],
) -> None:
    """Envelopes with missing or extra fields are rejected.

    Args:
        payload: Malformed envelope to store.
    """
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
def test_cache_rejects_duplicate_json_object_keys(payload: str) -> None:
    """JSON objects that repeat a key are rejected.

    Args:
        payload: JSON text with a repeated key.
    """
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
def test_cache_rejects_non_string_template_fields(
    field: str, replacement: object
) -> None:
    """Template fields that are not strings are rejected.

    Args:
        field: Template field that is replaced.
        replacement: Non-string value that replaces the field.
    """
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
def test_cache_rejects_unexpected_json_values_and_states(payload: object) -> None:
    """JSON values that are not envelopes, and unknown states, are rejected.

    Args:
        payload: Unexpected JSON value to store.
    """
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
def test_cache_rejects_non_string_backend_payloads(payload: object) -> None:
    """Backend values that are not strings are rejected.

    Args:
        payload: Non-string backend value to store.
    """
    cache = TemplateCache(f"backend-{type(payload).__name__}", alias="screens")
    store_raw(cache, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get(*LOOKUP)


@pytest.mark.parametrize("alias", [None, True, "", [], {}, ()])
def test_cache_normalizes_invalid_alias_types(alias: object) -> None:
    """Invalid alias types raise the stable invalid alias error.

    Args:
        alias: Invalid alias value.
    """
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias=alias)

    assert captured.value.source == "cache"
    assert captured.value.reason == "invalid alias"
    assert str(captured.value) == "Template source unavailable: cache (invalid alias)"


@override_settings(CACHES=BROKEN_CACHES)
def test_cache_normalizes_invalid_backend_configuration_without_leaking_cause() -> None:
    """A broken backend raises a generic error without its cause."""
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias="broken")

    assert captured.value.source == "cache:broken"
    assert captured.value.reason == "backend failure"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("kwargs", [{"ttl": True}, {"negative_ttl": True}])
def test_cache_rejects_boolean_timeouts(kwargs: dict[str, bool]) -> None:
    """Boolean TTL values are rejected as non-integers.

    Args:
        kwargs: Constructor argument that is a boolean.
    """
    with pytest.raises(ValueError, match="must be an integer"):
        TemplateCache("tenant", **kwargs)


def test_cache_still_accepts_the_exact_supported_miss_envelope() -> None:
    """The exact supported miss envelope is still accepted."""
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
