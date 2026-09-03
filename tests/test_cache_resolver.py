from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "resolver-cache",
    }
}
STUB_SOURCE = {"BACKEND": "tests.stubs.TemplateSource"}


class RecordingSource:
    def __init__(self, content: str | None, *, source="memory", revision="r1"):
        self.content = content
        self.source = source
        self.revision = revision
        self.calls = []

    def resolve(self, name):
        self.calls.append(name)
        if self.content is None:
            return None
        return ResolvedTemplate(
            name, self.content, f"memory:{name}", self.source, self.revision
        )


@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_hit_preserves_empty_content_without_source_lookup():
    cache = TemplateCache("empty-hit", alias="screens")
    source = RecordingSource("", source="database", revision="publish:42")
    resolver = TemplateResolver([source], cache=cache)

    first = resolver.resolve("screen.xml")
    source.content = "changed"
    second = resolver.resolve("screen.xml")
    assert first == second
    assert second.content == ""
    assert source.calls == ["screen.xml"]


@override_settings(CACHES=LOCMEM_CACHES)
def test_cached_source_miss_skips_only_that_source():
    cache = TemplateCache("source-miss", alias="screens")
    missing = RecordingSource(None, source="first")
    winner = RecordingSource("winner", source="second")
    resolver = TemplateResolver([missing, winner], cache=cache)
    assert resolver.resolve("screen.xml").content == "winner"
    assert resolver.resolve("screen.xml").content == "winner"
    assert missing.calls == ["screen.xml"]
    assert winner.calls == ["screen.xml"]


@pytest.mark.parametrize("operation", ["get", "set", "set_miss"])
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_operation_failure_obeys_policy(
    monkeypatch, operation, failure_mode
):
    cache = TemplateCache(f"{operation}-{failure_mode}", alias="screens")
    first = RecordingSource(None if operation == "set_miss" else "source")
    winner = RecordingSource("winner")
    sources = [first, winner] if operation == "set_miss" else [first]
    resolver = TemplateResolver(sources, cache=cache, failure_mode=failure_mode)
    method = "get" if operation == "get" else "set"
    monkeypatch.setattr(cache.backend, method, lambda *args, **kwargs: 1 / 0)
    if failure_mode == "raise":
        with pytest.raises(SourceUnavailable, match="cache:screens"):
            resolver.resolve("screen.xml")
        assert winner.calls == []
    else:
        expected = "winner" if operation == "set_miss" else "source"
        assert resolver.resolve("screen.xml").content == expected
    expected_calls = (
        [] if operation == "get" and failure_mode == "raise" else ["screen.xml"]
    )
    assert first.calls == expected_calls


@override_settings(CACHES=LOCMEM_CACHES)
def test_bypass_does_not_hide_a_real_source_failure(monkeypatch):
    cache = TemplateCache("source-failure", alias="screens")
    source = RecordingSource("unused")
    resolver = TemplateResolver([source], cache=cache, failure_mode="bypass")
    monkeypatch.setattr(cache.backend, "get", lambda *args, **kwargs: 1 / 0)
    source.resolve = lambda name: (_ for _ in ()).throw(
        SourceUnavailable("origin", "offline")
    )
    with pytest.raises(SourceUnavailable) as captured:
        resolver.resolve("screen.xml")
    assert captured.value.source == "origin"


@override_settings(
    HYPERVIEW={"SOURCES": [{**STUB_SOURCE, "OPTIONS": {"content": "ok"}}]}
)
def test_resolver_without_cache_configuration_does_not_create_cache():
    with patch(
        "dj_hyperview.resolver.TemplateCache.from_settings",
        side_effect=AssertionError("cache should remain opt-in"),
    ):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "ok"


@override_settings(CACHES=LOCMEM_CACHES)
def test_settings_cache_is_shared_by_source_identity():
    configured = {
        "SOURCES": [STUB_SOURCE],
        "CACHE": {
            "ALIAS": "screens",
            "NAMESPACE": "configured-resolver",
            "TTL": 40,
            "NEGATIVE_TTL": 4,
            "FAILURE_MODE": "raise",
        },
    }
    first = RecordingSource("first", revision="one")
    second = RecordingSource("second", revision="two")
    with override_settings(HYPERVIEW=configured):
        with patch("dj_hyperview.resolver.import_string", return_value=lambda: first):
            resolver = TemplateResolver.from_settings()
            initial = resolver.resolve("screen.xml")
        with patch("dj_hyperview.resolver.import_string", return_value=lambda: second):
            cached = TemplateResolver.from_settings().resolve("screen.xml")
    assert cached == initial
    assert second.calls == []
    assert resolver.cache.namespace == "configured-resolver"
    assert resolver.cache.ttl == 40
    assert resolver.cache.negative_ttl == 4


@pytest.mark.parametrize(
    ("failure_mode", "should_raise"), [("bypass", False), ("raise", True)]
)
def test_resolver_cache_initialization_failure_obeys_policy(failure_mode, should_raise):
    caches = {"broken": {"BACKEND": "tests.test_cache_fail_closed.ExplodingInitCache"}}
    configured = {
        "SOURCES": [{**STUB_SOURCE, "OPTIONS": {"content": "source"}}],
        "CACHE": {
            "ALIAS": "broken",
            "NAMESPACE": "init-failure",
            "FAILURE_MODE": failure_mode,
        },
    }
    with override_settings(CACHES=caches, HYPERVIEW=configured):
        if should_raise:
            with pytest.raises(SourceUnavailable, match="backend failure"):
                TemplateResolver.from_settings()
        else:
            assert TemplateResolver.from_settings().resolve("screen.xml").content == (
                "source"
            )


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_corrupt_entry_obeys_policy(failure_mode):
    cache = TemplateCache(f"corrupt-{failure_mode}", alias="screens")
    source = RecordingSource("source")
    resolver = TemplateResolver([source], cache=cache, failure_mode=failure_mode)
    source_id = "0:tests.test_cache_resolver.RecordingSource"
    cache.backend.set(cache.key(source_id, "screen.xml", "@resolved"), "invalid-json")
    if failure_mode == "raise":
        with pytest.raises(SourceUnavailable, match="invalid payload"):
            resolver.resolve("screen.xml")
        assert source.calls == []
    else:
        assert resolver.resolve("screen.xml").content == "source"


@pytest.mark.parametrize("state", ["resolved", "source-miss"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_envelope_cannot_move_between_source_identities(state):
    cache = TemplateCache(f"identity-{state}", alias="screens")
    template = ResolvedTemplate("screen.xml", "raw", "memory:x", "memory", "r1")
    if state == "resolved":
        cache.set_resolved("first", "screen.xml", template)
    else:
        cache.set_resolved_miss("first", "screen.xml")
    payload = cache.backend.get(cache.key("first", "screen.xml", "@resolved"))
    cache.backend.set(cache.key("second", "screen.xml", "@resolved"), payload)
    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.get_resolved("second", "screen.xml")


@pytest.mark.parametrize("namespace", ["", None, True])
def test_cache_namespace_configuration_must_be_a_non_empty_string(namespace):
    from dj_hyperview.checks import check_hyperview_settings

    with override_settings(HYPERVIEW={"CACHE": {"NAMESPACE": namespace}}):
        errors = check_hyperview_settings()
    assert [error.id for error in errors] == ["dj_hyperview.E004"]
    assert errors[0].msg == "CACHE.NAMESPACE must be a non-empty string."
