"""Cache invalidation rotates generations safely under races and failures."""

import traceback
from collections.abc import Callable
from threading import Barrier, Event, Lock, Thread
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview import invalidate_templates
from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import (
    InvalidTemplateName,
    SourceUnavailable,
    TemplateNotFound,
)
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "generation-race",
    }
}


def config(namespace: str, failure_mode: str = "raise") -> dict[str, dict[str, str]]:
    """Build the Hyperview settings of a cache in a namespace.

    Args:
        namespace: Cache namespace to configure.
        failure_mode: Cache failure mode to configure.

    Returns:
        The HYPERVIEW settings mapping with the cache section.
    """
    return {
        "CACHE": {
            "ALIAS": "screens",
            "NAMESPACE": namespace,
            "FAILURE_MODE": failure_mode,
        }
    }


class Source:
    """Template source with scripted contents that can block readers on demand.

    Args:
        values: Template text by name, None for a missing template.
        readers: Number of resolutions that block until released.
    """

    def __init__(self, values: dict[str, str | None], readers: int = 0) -> None:
        """Keep the contents and prepare the blocking barrier."""
        self.values, self.calls = values, []
        self.started = Barrier(readers + 1) if readers else None
        self.release, self.remaining, self.lock = Event(), readers, Lock()

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Record the call, optionally block until released, then serve the content.

        Args:
            name: Template name to resolve.

        Returns:
            The resolved template, or None when the name has no content.
        """
        self.calls.append(name)
        with self.lock:
            content, blocks = self.values.get(name), self.remaining > 0
            self.remaining -= blocks
        if blocks:
            self.started.wait(timeout=3)
            assert self.release.wait(timeout=3)
        if content is None:
            return None
        return ResolvedTemplate(name, content, f"memory:{name}", "memory", content)


def resolver(
    source: Source, namespace: str, failure_mode: str = "raise"
) -> TemplateResolver:
    """Build a resolver over one source and a cache in the namespace.

    Args:
        source: Source the resolver reads.
        namespace: Cache namespace of the resolver.
        failure_mode: Cache failure mode of the resolver.

    Returns:
        A resolver bound to the source and the cache.
    """
    return TemplateResolver(
        [source],
        cache=TemplateCache(namespace, alias="screens"),
        failure_mode=failure_mode,
        _source_ids=["source:test"],
    )


def launch(call: Callable[[], object]) -> tuple[Thread, dict[str, object]]:
    """Run a call in a started thread and collect its outcome.

    Args:
        call: Function to run in the thread.

    Returns:
        The started thread and a dictionary that receives the value or the error.
    """
    outcome = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except Exception as error:  # noqa: BLE001 - the caller asserts the error.
            outcome["error"] = error

    thread = Thread(target=target)
    thread.start()
    return thread, outcome


@override_settings(CACHES=LOCMEM_CACHES)
def test_invalidation_isolates_name_namespace_content_miss_and_eviction() -> None:
    """Invalidation affects only the named templates of its own namespace."""
    namespace = "rotate-content-miss"
    source = Source({"screen.xml": "old", "missing.xml": None, "other.xml": "stable"})
    current, isolated = resolver(source, namespace), resolver(source, "isolated")
    assert current.resolve("screen.xml").content == "old"
    assert isolated.resolve("screen.xml").content == "old"
    assert current.resolve("other.xml").content == "stable"
    with pytest.raises(TemplateNotFound):
        current.resolve("missing.xml")

    source.values.update(
        {"screen.xml": "new", "missing.xml": "found", "other.xml": "changed"}
    )
    with override_settings(HYPERVIEW=config(namespace)):
        invalidate_templates("screen.xml", "missing.xml")
    assert current.resolve("screen.xml").content == "new"
    assert current.resolve("missing.xml").content == "found"
    assert current.resolve("other.xml").content == "stable"
    assert isolated.resolve("screen.xml").content == "old"
    current.cache.backend.delete(
        current.cache.key("@generation", "other.xml", "@token")
    )
    assert current.resolve("other.xml").content == "changed"


@pytest.mark.parametrize("old", ["old", None])
@override_settings(CACHES=LOCMEM_CACHES)
def test_concurrent_old_readers_and_invalidations_never_repopulate(
    old: str | None,
) -> None:
    """Readers that started before an invalidation never repopulate the cache.

    Args:
        old: Old template text, or None for a template that was missing.
    """
    namespace = f"race-{'miss' if old is None else 'content'}"
    source = Source({"screen.xml": old}, readers=3)
    current = resolver(source, namespace, failure_mode="bypass")
    readers = [launch(lambda: current.resolve("screen.xml")) for _ in range(3)]
    source.started.wait(timeout=3)
    gate = Barrier(3)

    def rotate() -> None:
        gate.wait(timeout=3)
        invalidate_templates("screen.xml")

    with override_settings(HYPERVIEW=config(namespace)):
        invalidators = [launch(rotate) for _ in range(2)]
        gate.wait(timeout=3)
        for thread, outcome in invalidators:
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert outcome == {"value": None}
    source.values["screen.xml"] = "new"
    source.release.set()
    for thread, outcome in readers:
        thread.join(timeout=3)
        assert not thread.is_alive()
        if old is None:
            assert isinstance(outcome["error"], TemplateNotFound)
        else:
            assert outcome["value"].content == "old"
    assert current.resolve("screen.xml").content == "new"


@override_settings(CACHES=LOCMEM_CACHES)
def test_names_validate_before_effects_and_duplicates_rotate_once() -> None:
    """Names are validated before any effect and duplicates rotate only once."""
    unsafe_name = "../private-screen.xml"
    with override_settings(HYPERVIEW=config("validation")):
        with patch.object(TemplateCache, "invalidate") as rotate:
            with pytest.raises(InvalidTemplateName) as captured:
                invalidate_templates("screen.xml", unsafe_name)
            error = captured.value
            rendered = "".join(traceback.format_exception(error))
            assert error.__cause__ is None
            assert error.__context__ is None
            assert unsafe_name not in str(error)
            assert unsafe_name not in repr(error)
            assert unsafe_name not in rendered
            assert rotate.call_count == 0
            invalidate_templates("screen.xml", "screen.xml")
            rotate.assert_called_once_with("screen.xml")


def test_noop_or_disabled_cache_never_initializes_backend() -> None:
    """Invalidating nothing, or with the cache disabled, never builds the cache."""
    with patch(
        "dj_hyperview.cache.TemplateCache.from_settings",
        side_effect=AssertionError("cache must stay disabled"),
    ):
        invalidate_templates()
        with override_settings(HYPERVIEW={}):
            invalidate_templates("screen.xml")


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_invalidation_initialization_failure_obeys_policy(failure_mode: str) -> None:
    """A cache that fails to start fails the invalidation in both failure modes.

    Args:
        failure_mode: Cache failure mode that is configured.
    """
    error = SourceUnavailable("cache:screens", "backend failure")
    with override_settings(HYPERVIEW=config("init-failure", failure_mode)):
        with patch.object(TemplateCache, "from_settings", side_effect=error):
            with pytest.raises(SourceUnavailable, match="backend failure"):
                invalidate_templates("screen.xml")


@pytest.mark.parametrize("operation", ["read", "write-false", "write-error", "payload"])
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_generation_failures_are_safe_and_obey_policy(
    monkeypatch: pytest.MonkeyPatch, operation: str, failure_mode: str
) -> None:
    """Generation failures stay safe and follow the failure mode.

    Args:
        monkeypatch: Makes a cache backend operation fail for this test.
        operation: Failing step: read, write-false, write-error or payload.
        failure_mode: Cache failure mode that is configured.
    """
    namespace = f"{operation}-failure-{failure_mode}"
    source = Source({"screen.xml": "old"})
    current = resolver(source, namespace, failure_mode)
    if operation.startswith("write"):
        assert current.resolve("screen.xml").content == "old"
        source.values["screen.xml"] = "new"
        method, call = "set", lambda: invalidate_templates("screen.xml")
    elif operation == "payload":
        key = current.cache.key("@generation", "screen.xml", "@token")
        current.cache.backend.set(key, "invalid")
        method, call = None, lambda: current.resolve("screen.xml")
    else:
        method, call = "get", lambda: current.resolve("screen.xml")
    if method:
        failure = (
            (lambda *args, **kwargs: False)
            if operation == "write-false"
            else (lambda *args, **kwargs: 1 / 0)
        )
        monkeypatch.setattr(current.cache.backend, method, failure)

    with override_settings(HYPERVIEW=config(namespace, failure_mode)):
        with patch.object(TemplateCache, "from_settings", return_value=current.cache):
            if operation.startswith("write") or failure_mode == "raise":
                reason = (
                    "invalid payload" if operation == "payload" else "backend failure"
                )
                with pytest.raises(SourceUnavailable, match=reason):
                    call()
            else:
                assert call().content == "old"
