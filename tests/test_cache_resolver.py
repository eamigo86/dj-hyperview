"""The resolver reads through the template cache and obeys its failure mode."""

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
    """Cacheable source that records the names it resolves.

    Args:
        content: Template text to serve, or None to resolve nothing.
        source: Source label of the resolved templates.
        revision: Revision of the resolved templates.
    """

    cacheable = True

    def __init__(
        self, content: str | None, *, source: str = "memory", revision: str = "r1"
    ) -> None:
        """Keep the content, source label and revision."""
        self.content = content
        self.source = source
        self.revision = revision
        self.calls = []

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Record the name and serve the content when there is some.

        Args:
            name: Template name to resolve.

        Returns:
            The resolved template, or None when the source has no content.
        """
        self.calls.append(name)
        if self.content is None:
            return None
        return ResolvedTemplate(
            name, self.content, f"memory:{name}", self.source, self.revision
        )


class UncacheableRecordingSource(RecordingSource):
    """Recording source that opts out of caching."""

    cacheable = False


class BooleanSafeRecordingSource(RecordingSource):
    """Recording source that marks itself cache safe with a boolean."""

    _dj_hyperview_cache_safe = True


@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_hit_preserves_empty_content_without_source_lookup() -> None:
    """A cache hit keeps empty content and skips the source."""
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
def test_cached_source_miss_skips_only_that_source() -> None:
    """A cached miss skips only the source that missed."""
    cache = TemplateCache("source-miss", alias="screens")
    missing = RecordingSource(None, source="first")
    winner = RecordingSource("winner", source="second")
    resolver = TemplateResolver([missing, winner], cache=cache)
    assert resolver.resolve("screen.xml").content == "winner"
    assert resolver.resolve("screen.xml").content == "winner"
    assert missing.calls == ["screen.xml"]
    assert winner.calls == ["screen.xml"]


@override_settings(CACHES=LOCMEM_CACHES)
def test_initial_cacheable_miss_is_published_before_uncacheable_hit() -> None:
    """A later uncacheable winner does not discard earlier cache-safe misses."""
    cache = TemplateCache("mixed-initial-publication", alias="screens")
    missing = RecordingSource(None, source="filesystem")
    winner = UncacheableRecordingSource("database", source="database")
    resolver = TemplateResolver([missing, winner], cache=cache)

    assert resolver.resolve("screen.xml").content == "database"
    assert resolver.resolve("screen.xml").content == "database"

    assert missing.calls == ["screen.xml"]
    assert winner.calls == ["screen.xml", "screen.xml"]


@override_settings(CACHES=LOCMEM_CACHES)
def test_boolean_cache_safety_marker_is_supported() -> None:
    """Cache safety markers accept the same bool shape as cacheability markers."""
    cache = TemplateCache("boolean-cache-safety", alias="screens")
    source = BooleanSafeRecordingSource("first")
    resolver = TemplateResolver([source], cache=cache)

    assert resolver.resolve("screen.xml").content == "first"
    source.content = "second"
    assert resolver.resolve("screen.xml").content == "first"
    assert source.calls == ["screen.xml"]


@pytest.mark.parametrize("operation", ["get", "set", "set_miss"])
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_operation_failure_obeys_policy(
    monkeypatch: pytest.MonkeyPatch, operation: str, failure_mode: str
) -> None:
    """A failing cache operation follows the failure mode.

    Args:
        monkeypatch: Makes a cache backend operation fail for this test.
        operation: Cache operation that fails: get, set or set_miss.
        failure_mode: Cache failure mode of the resolver.
    """
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
        expected_winner_calls = ["screen.xml"] if operation == "set_miss" else []
        assert winner.calls == expected_winner_calls
    else:
        expected = "winner" if operation == "set_miss" else "source"
        assert resolver.resolve("screen.xml").content == expected
    expected_calls = (
        [] if operation == "get" and failure_mode == "raise" else ["screen.xml"]
    )
    assert first.calls == expected_calls


@override_settings(CACHES=LOCMEM_CACHES)
def test_bypass_does_not_hide_a_real_source_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypass mode never hides a failure of the real source.

    Args:
        monkeypatch: Makes the cache read fail for this test.
    """
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
def test_resolver_without_cache_configuration_does_not_create_cache() -> None:
    """Without cache settings the resolver never builds a cache."""
    with patch(
        "dj_hyperview.resolver.TemplateCache.from_settings",
        side_effect=AssertionError("cache should remain opt-in"),
    ):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "ok"


@override_settings(CACHES=LOCMEM_CACHES)
def test_settings_cache_is_shared_by_source_identity() -> None:
    """Resolvers built from settings share cached entries by source identity."""
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
def test_resolver_cache_initialization_failure_obeys_policy(
    failure_mode: str, should_raise: bool
) -> None:
    """A cache that fails to start follows the failure mode.

    Args:
        failure_mode: Cache failure mode that is configured.
        should_raise: Whether building the resolver must raise.
    """
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
def test_resolver_corrupt_entry_obeys_policy(failure_mode: str) -> None:
    """A corrupt cache entry follows the failure mode.

    Args:
        failure_mode: Cache failure mode of the resolver.
    """
    cache = TemplateCache(f"corrupt-{failure_mode}", alias="screens")
    source = RecordingSource("source")
    resolver = TemplateResolver([source], cache=cache, failure_mode=failure_mode)
    source_id = resolver._source_ids[0]
    generation = cache.generation("screen.xml")
    revision = cache._resolved_revision(generation)
    cache.backend.set(cache.key(source_id, "screen.xml", revision), "invalid-json")
    if failure_mode == "raise":
        with pytest.raises(SourceUnavailable, match="invalid payload"):
            resolver.resolve("screen.xml")
        assert source.calls == []
    else:
        assert resolver.resolve("screen.xml").content == "source"


@pytest.mark.parametrize("state", ["resolved", "source-miss"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_cache_envelope_cannot_move_between_source_identities(
    state: str,
) -> None:
    """A cached envelope copied to another source identity is rejected.

    Args:
        state: Kind of envelope that is copied: resolved or source-miss.
    """
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
def test_cache_namespace_configuration_must_be_a_non_empty_string(
    namespace: object,
) -> None:
    """The cache namespace setting must be a non-empty string.

    Args:
        namespace: Invalid namespace value.
    """
    from dj_hyperview.checks import check_hyperview_settings

    with override_settings(HYPERVIEW={"CACHE": {"NAMESPACE": namespace}}):
        errors = check_hyperview_settings()
    namespace_error = next(error for error in errors if error.id == "dj_hyperview.E004")
    assert namespace_error.msg == "CACHE.NAMESPACE must be a non-empty string."
