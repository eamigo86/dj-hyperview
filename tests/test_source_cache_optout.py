"""Caching is an explicit opt-in: only an exact True cacheable attribute enables it.

These tests migrate the protections of the former private marker to the public
attribute: strict bool domain, fail-closed unreadable attributes, one read per
source identity, no leakage of attribute errors and no swallowed base exceptions.
"""

import traceback
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-cache-optout",
    }
}


class RecordingSource:
    """Source that records its calls and returns fixed content or fails.

    Args:
        content: Content that the source returns, or None for a missing template.
        failure: Error that every resolve raises, or None to resolve normally.
    """

    def __init__(
        self,
        content: str | None,
        *,
        failure: SourceUnavailable | None = None,
    ) -> None:
        """Store the content, the failure and an empty call count."""
        self.content = content
        self.failure = failure
        self.calls = 0

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Count the call, then fail, miss or return the fixed content.

        Args:
            name: Name of the template to resolve.

        Returns:
            The resolved template, or None when the source has no content.
        """
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        if self.content is None:
            return None
        return ResolvedTemplate(name, self.content, "memory", "memory", "1")


class CacheableRecordingSource(RecordingSource):
    """Recording source that opts in to caching with an exact True attribute."""

    cacheable = True


class ExplodingMarkerSource(RecordingSource):
    """Recording source whose cacheable attribute fails when it is read.

    Args:
        marker_error: Kind of failure to raise: attribute, runtime or a base exception.
        **kwargs: Arguments passed to the recording source.
    """

    def __init__(self, marker_error: str, **kwargs: object) -> None:
        """Store the failure kind and an empty read count."""
        super().__init__(**kwargs)
        self.marker_error = marker_error
        self.marker_reads = 0

    @property
    def cacheable(self) -> bool:
        """Count the read, then fail as configured.

        Raises:
            AttributeError: When the configured failure is an attribute error.
            RuntimeError: When the configured failure is a runtime error.
            KeyboardInterrupt: When the configured failure is a base exception.
        """
        self.marker_reads += 1
        if self.marker_error == "attribute":
            raise AttributeError("unreadable attribute")
        if self.marker_error == "runtime":
            raise RuntimeError("SENSITIVE-MARKER-DETAIL")
        raise KeyboardInterrupt


class CountingMarkerSource(RecordingSource):
    """Recording source that counts how often its cacheable attribute is read.

    Args:
        **kwargs: Arguments passed to the recording source.
    """

    def __init__(self, **kwargs: object) -> None:
        """Store an empty read count."""
        super().__init__(**kwargs)
        self.marker_reads = 0

    @property
    def cacheable(self) -> bool:
        """Count the read and report that the source opts in."""
        self.marker_reads += 1
        return True


class UnsetSlotMarkerSource:
    """Source whose cacheable slot is declared but never assigned."""

    __slots__ = ("content", "calls", "cacheable")

    def __init__(self) -> None:
        """Store the content and an empty call count, leaving the slot unset."""
        self.content = "first"
        self.calls = 0

    def resolve(self, name: str) -> ResolvedTemplate:
        """Count the call and return the stored content.

        Args:
            name: Name of the template to resolve.

        Returns:
            The resolved template that holds the stored content.
        """
        self.calls += 1
        return ResolvedTemplate(name, self.content, "slot", "slot", "1")


@pytest.fixture(autouse=True)
def configured_cache() -> Iterator[None]:
    """Run the test with an empty in-memory screens cache.

    Yields:
        Nothing, while the in-memory cache settings are active.
    """
    with override_settings(CACHES=LOCMEM_CACHES):
        caches["screens"].clear()
        yield


def resolver(source: object, namespace: str = "marker") -> TemplateResolver:
    """Build a resolver over one source with a cache of its own.

    Args:
        source: Source that the resolver reads.
        namespace: Cache namespace of the resolver.

    Returns:
        A resolver that caches according to the source marker.
    """
    return TemplateResolver([source], cache=TemplateCache(namespace, alias="screens"))


@pytest.mark.parametrize(
    "marker",
    [None, 0, 1, "false", [], object(), False],
    ids=["none", "zero", "one", "string", "list", "object", "false"],
)
@pytest.mark.parametrize("initial", ["first", None], ids=["content", "miss"])
def test_only_exact_true_marker_enables_cache(
    marker: object, initial: str | None
) -> None:
    """Only an exact True marker enables caching.

    Args:
        marker: Value assigned to the cacheable attribute.
        initial: Content that the source returns first, or None for a miss.
    """
    source = RecordingSource(initial)
    source.cacheable = marker
    with patch("dj_hyperview.resolver._source_fingerprint", return_value="source:test"):
        current = resolver(
            source, f"closed-domain-{initial is None}-{type(marker).__name__}"
        )

    if initial is None:
        with pytest.raises(TemplateNotFound):
            current.resolve("screen.xml")
    else:
        assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "second"
    assert source.calls == 2


def test_absent_attribute_is_not_cached() -> None:
    """A source without the attribute is not cached."""
    source = RecordingSource("first")
    current = resolver(source, "absent")

    assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "second"
    assert source.calls == 2


def test_exact_true_attribute_is_cached() -> None:
    """A source with an exact True attribute is cached."""
    source = CacheableRecordingSource("first")
    current = resolver(source, "exact-true")

    assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "first"
    assert source.calls == 1


@pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
def test_legacy_private_marker_no_longer_selects_caching(value: bool) -> None:
    """The legacy private marker no longer selects caching.

    Args:
        value: Value assigned to the legacy private marker.
    """
    source = RecordingSource("first")
    source._dj_hyperview_cacheable = value
    current = resolver(source, f"legacy-{value}")

    assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "second"
    assert source.calls == 2


@pytest.mark.parametrize("marker_error", ["attribute", "runtime"])
def test_unreadable_marker_fails_closed_without_hiding_source_content(
    marker_error: str,
) -> None:
    """An unreadable marker fails closed and still serves the source content.

    Args:
        marker_error: Kind of failure that reading the marker raises.
    """
    source = ExplodingMarkerSource(marker_error, content="first")
    current = resolver(source, marker_error)

    assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "second"
    assert (source.marker_reads, source.calls) == (1, 2)


def test_unset_slot_marker_is_present_and_fails_closed() -> None:
    """An unset slot marker counts as present and fails closed."""
    source = UnsetSlotMarkerSource()
    with patch("dj_hyperview.resolver._source_fingerprint", return_value="source:test"):
        current = resolver(source, "unset-slot")

    assert current.resolve("screen.xml").content == "first"
    source.content = "second"

    assert current.resolve("screen.xml").content == "second"
    assert source.calls == 2


def test_marker_failure_does_not_hide_or_chain_real_source_failure() -> None:
    """A marker failure neither hides nor chains the real source failure."""
    failure = SourceUnavailable("origin", "actual source failure")
    source = ExplodingMarkerSource("runtime", content=None, failure=failure)
    current = resolver(source, "source-failure")

    with pytest.raises(SourceUnavailable) as captured:
        current.resolve("screen.xml")

    rendered = "".join(traceback.format_exception(captured.value))
    assert captured.value is failure
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "SENSITIVE-MARKER-DETAIL" not in rendered
    assert (source.marker_reads, source.calls) == (1, 1)


def test_marker_is_read_once_per_source_identity() -> None:
    """The marker is read once per source identity."""
    source = CountingMarkerSource(content="first")
    current = resolver(source, "read-once")

    assert current.resolve("screen.xml").content == "first"
    assert current.resolve("screen.xml").content == "first"
    assert (source.marker_reads, source.calls) == (1, 1)


def test_unreadable_marker_does_not_disable_other_source_cache() -> None:
    """An unreadable marker does not disable the cache of another source."""
    uncached = ExplodingMarkerSource("runtime", content=None)
    cached = CacheableRecordingSource("winner")
    current = TemplateResolver(
        [uncached, cached], cache=TemplateCache("per-source", alias="screens")
    )

    assert current.resolve("screen.xml").content == "winner"
    assert current.resolve("screen.xml").content == "winner"
    assert (uncached.calls, cached.calls) == (2, 1)


def test_marker_access_does_not_swallow_base_exceptions() -> None:
    """Reading the marker does not swallow base exceptions."""
    source = ExplodingMarkerSource("base", content="unused")

    with pytest.raises(KeyboardInterrupt):
        resolver(source, "base-exception")

    assert (source.marker_reads, source.calls) == (1, 0)
