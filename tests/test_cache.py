from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import override_settings

from dj_hyperview.cache import CACHE_MISS, CacheEntry, TemplateCache, template_cache_key
from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.sources import ResolvedTemplate

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "default-cache-contract",
    },
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "screens-cache-contract",
    },
}


def resolved(content="", *, revision="r1"):
    return ResolvedTemplate(
        name="screen.xml",
        content=content,
        origin="memory:screen.xml",
        source="memory",
        revision=revision,
    )


def test_template_cache_keys_are_deterministic_and_component_safe():
    components = [
        ("tenant:a", "source", "screen.xml", "rev"),
        ("tenant", "a:source", "screen.xml", "rev"),
        ("tenant", "a", "source:screen.xml", "rev"),
        ("tenant", "a", "source", "screen.xml:rev"),
        ("inquilino-ñ", "数据库", "pantallas/🦊.xml", "revisión-α"),
        ("tenant" * 500, "source" * 500, "screen.xml" * 500, "rev" * 500),
    ]

    keys = [template_cache_key(*value) for value in components]

    assert len(set(keys)) == len(components)
    assert keys[-1] == template_cache_key(*components[-1])
    assert all(key.isascii() and len(key) <= 250 for key in keys)


@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_distinguishes_absent_miss_and_empty_content():
    cache = TemplateCache("tenant", alias="screens")
    template = resolved()

    assert cache.get("memory", "screen.xml", "r1") is None

    cache.set_miss("memory", "screen.xml", "r1")
    miss = cache.get("memory", "screen.xml", "r1")
    assert miss is CACHE_MISS
    assert bool(miss) is True
    assert miss.is_miss is True

    cache.set(template)
    entry = cache.get("memory", "screen.xml", "r1")
    assert entry == CacheEntry(template)
    assert bool(entry) is True
    assert entry.is_miss is False
    assert entry.template.content == ""


@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_preserves_content_and_resolved_metadata():
    cache = TemplateCache("tenant", alias="screens")
    template = resolved("<view>cached</view>", revision="sha256:abc")

    cache.set(template)

    assert cache.get("memory", "screen.xml", "sha256:abc") == CacheEntry(template)


@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_namespaces_do_not_overwrite_each_other():
    first = TemplateCache("consumer-one", alias="screens")
    second = TemplateCache("consumer-two", alias="screens")
    template = resolved("<view />")

    first.set(template)

    assert first.get("memory", "screen.xml", "r1") == CacheEntry(template)
    assert second.get("memory", "screen.xml", "r1") is None


@pytest.mark.parametrize(
    ("store", "ttl"),
    [("template", 5), ("miss", 2)],
)
@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_entries_expire_at_the_configured_ttl_without_sleep(store, ttl):
    cache = TemplateCache(f"expiry-{store}", alias="screens", ttl=5, negative_ttl=2)
    template = resolved()
    key = ("memory", "screen.xml", "r1")

    with patch("django.core.cache.backends.base.time.time", return_value=100.0):
        cache.set(template) if store == "template" else cache.set_miss(*key)
    with patch(
        "django.core.cache.backends.locmem.time.time", return_value=100.0 + ttl - 0.1
    ):
        assert cache.get(*key) is not None
    with patch("django.core.cache.backends.locmem.time.time", return_value=100.0 + ttl):
        assert cache.get(*key) is None


@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_uses_validated_django_settings():
    configured = {
        "CACHE": {
            "ALIAS": "screens",
            "TTL": 45,
            "NEGATIVE_TTL": 6,
            "FAILURE_MODE": "raise",
        }
    }

    with override_settings(HYPERVIEW=configured):
        cache = TemplateCache.from_settings("tenant")

    assert cache.alias == "screens"
    assert cache.ttl == 45
    assert cache.negative_ttl == 6


@override_settings(CACHES=LOCMEM_CACHES)
def test_unknown_cache_alias_raises_stable_public_error():
    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("tenant", alias="missing")

    assert captured.value.source == "cache:missing"
    assert captured.value.reason == "unknown alias"
    assert str(captured.value) == (
        "Template source unavailable: cache:missing (unknown alias)"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"namespace": ""}, "Cache namespace must be a non-empty string"),
        ({"namespace": None}, "Cache namespace must be a non-empty string"),
        ({"namespace": "tenant", "ttl": 0}, "Cache TTL must be an integer >= 1"),
        ({"namespace": "tenant", "ttl": None}, "Cache TTL must be an integer >= 1"),
        (
            {"namespace": "tenant", "negative_ttl": -1},
            "Cache negative TTL must be an integer >= 0",
        ),
    ],
)
@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_rejects_invalid_direct_configuration(kwargs, message):
    with pytest.raises(ValueError, match=f"^{message}$"):
        TemplateCache(**kwargs)


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"version":1,"state":"miss","unexpected":true}',
        '{"version":1,"state":"template","template":{"name":1}}',
    ],
)
@override_settings(CACHES=LOCMEM_CACHES)
def test_corrupt_cache_payload_raises_stable_public_error(payload):
    cache = TemplateCache("tenant", alias="screens")
    key = cache.key("memory", "screen.xml", "r1")
    caches["screens"].set(key, payload)

    with pytest.raises(SourceUnavailable) as captured:
        cache.get("memory", "screen.xml", "r1")

    assert captured.value.source == "cache:screens"
    assert captured.value.reason == "invalid payload"
    assert str(captured.value) == (
        "Template source unavailable: cache:screens (invalid payload)"
    )


def test_cache_contract_is_exported_from_package_root():
    import dj_hyperview

    assert dj_hyperview.CACHE_MISS is CACHE_MISS
    assert dj_hyperview.CacheEntry is CacheEntry
    assert dj_hyperview.SourceUnavailable is SourceUnavailable
    assert dj_hyperview.TemplateCache is TemplateCache
    assert dj_hyperview.template_cache_key is template_cache_key
