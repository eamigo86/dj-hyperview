from threading import Event
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate
from tests.test_cache_generation import LOCMEM_CACHES, Source, launch


class ClockBackend:
    def __init__(self):
        self.now, self.values, self.faults, self.after_set = 0, {}, {}, None

    def _fault(self, operation, key):
        effect = self.faults.get((operation, key))
        if effect == "exception":
            raise RuntimeError("secret cache payload")
        return effect == "false"

    def _live(self, key):
        item = self.values.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= self.now:
            self.values.pop(key, None)
            return None
        return value

    def get(self, key, default=None):
        if self._fault("get", key):
            return False
        value = self._live(key)
        return default if value is None else value

    def add(self, key, value, timeout=None):
        if self._fault("add", key) or self._live(key) is not None:
            return False
        self._put(key, value, timeout)
        return True

    def set(self, key, value, timeout=None):
        if self._fault("set", key):
            return False
        self._put(key, value, timeout)
        if self.after_set is not None:
            self.after_set(key)
        return True

    def delete(self, key):
        if self._fault("delete", key):
            return False
        return self.values.pop(key, None) is not None

    def _put(self, key, value, timeout):
        expires = None if timeout is None else self.now + timeout
        self.values[key] = (value, expires)

    def expiry(self, key):
        self._live(key)
        return self.values[key][1]

    def advance(self, amount):
        self.now += amount


@pytest.fixture
def bind_backend(monkeypatch):
    """Bind one deterministic backend without changing production cache ownership."""

    def bind(backend):
        monkeypatch.setattr(
            "dj_hyperview.cache._resolve_cache_alias", lambda alias: (backend, None)
        )

    return bind


@override_settings(CACHES=LOCMEM_CACHES)
def test_unknown_template_misses_create_no_permanent_generation_metadata(bind_backend):
    """Attacker-controlled misses cannot grow cache metadata."""
    cache = TemplateCache("unknown-no-metadata", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)
    current = resolver(Source({}), cache, "raise")

    with pytest.raises(TemplateNotFound):
        current.resolve("unknown.xml")

    assert backend.values == {}


@override_settings(CACHES=LOCMEM_CACHES)
def test_first_known_template_claims_one_generation_and_publishes_content(bind_backend):
    """A real source hit creates only the reusable generation and raw entry."""
    cache = TemplateCache("known-generation", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)
    source = Source({"screen.xml": "content"})
    current = resolver(source, cache, "raise")

    assert current.resolve("screen.xml").content == "content"
    assert current.resolve("screen.xml").content == "content"

    assert source.calls == ["screen.xml"]
    assert len(backend.values) == 2
    assert not any("generation-claim" in key for key in backend.values)


@override_settings(CACHES=LOCMEM_CACHES)
def test_concurrent_rotation_past_our_candidate_is_still_successful(bind_backend):
    """Another valid rotation after ours confirms invalidation, not failure."""
    cache = TemplateCache("concurrent-rotation", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)
    current = cache.generation("screen.xml")
    successor = "s" + "f" * 32
    generation_key = cache._generation_key("screen.xml")

    def supersede(key):
        if key == generation_key:
            backend._put(key, successor, None)

    backend.after_set = supersede
    cache.invalidate("screen.xml")

    assert current != successor
    assert cache.generation("screen.xml") == successor


@override_settings(CACHES=LOCMEM_CACHES)
def test_deleting_an_already_absent_raw_entry_is_successful(bind_backend):
    """A healthy cache miss from delete is not a backend failure."""
    cache = TemplateCache("delete-absent", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)

    cache._delete("missing-key")


class BlockingSource:
    def __init__(self, value):
        self.value, self.started, self.release = value, Event(), Event()

    def resolve(self, name):
        value = self.value
        self.started.set()
        assert self.release.wait(timeout=3)
        if value is None:
            return None
        return ResolvedTemplate(name, value, f"memory:{name}", "memory", value)


def resolver(source, cache, mode="bypass"):
    return TemplateResolver(
        [source], cache=cache, failure_mode=mode, _source_ids=["source:test"]
    )


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("transition", ["eviction", "rotations"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_inflight_writer_cannot_publish_into_reused_generation(
    old, transition, bind_backend
):
    cache = TemplateCache(
        f"inflight-{old}-{transition}", alias="screens", ttl=10, negative_ttl=5
    )
    backend = ClockBackend()
    bind_backend(backend)
    source = BlockingSource(old)
    current = resolver(source, cache)

    with patch("dj_hyperview.cache.secrets.token_hex", return_value="a" * 32):
        thread, outcome = launch(lambda: current.resolve("screen.xml"))
        assert source.started.wait(timeout=3)
    initial = cache.generation("screen.xml")
    if transition == "eviction":
        backend.advance(11)
        backend.delete(cache.key("@generation", "screen.xml", "@token"))
        entropy = ["a" * 32, "b" * 32]
        with patch("dj_hyperview.cache.secrets.token_hex", side_effect=entropy):
            cache.generation("screen.xml")
    else:
        with patch("dj_hyperview.cache.secrets.token_hex", return_value="b" * 32):
            cache.invalidate("screen.xml")
        backend.advance(11)
        with patch("dj_hyperview.cache.secrets.token_hex", return_value="a" * 32):
            cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") != initial

    source.value = "new"
    source.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    if old is None:
        assert isinstance(outcome["error"], TemplateNotFound)
    else:
        assert outcome["value"].content == "old"
    assert resolver(source, cache).resolve("screen.xml").content == "new"


def arm_rotation(cache, backend, generation):
    raw_key = cache.key(
        "source:test", "screen.xml", cache._resolved_revision(generation)
    )
    generation_key = cache.key("@generation", "screen.xml", "@token")
    successor = "s" + "f" * 32

    def rotate(key):
        if key == raw_key:
            backend._put(generation_key, successor, None)

    backend.after_set = rotate
    return raw_key, generation_key


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_handles_rotation_between_raw_write_and_confirmation(
    old, mode, bind_backend
):
    cache = TemplateCache(f"publication-{old}-{mode}", alias="screens", ttl=10)
    backend = ClockBackend()
    bind_backend(backend)
    source = Source({"screen.xml": old})
    generation = cache.generation("screen.xml")
    raw_key, _ = arm_rotation(cache, backend, generation)

    if old is None:
        with pytest.raises(TemplateNotFound):
            resolver(source, cache, mode).resolve("screen.xml")
    else:
        assert resolver(source, cache, mode).resolve("screen.xml").content == "old"
    assert backend.get(raw_key, "absent") == "absent"


@override_settings(CACHES=LOCMEM_CACHES)
def test_cache_reports_cleanly_superseded_and_current_publications(bind_backend):
    """A clean generation race is an outcome, not a cache failure."""
    cache = TemplateCache("publication-outcome", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)
    generation = cache.generation("screen.xml")
    raw_key, _ = arm_rotation(cache, backend, generation)
    template = ResolvedTemplate(
        "screen.xml", "authoritative", "memory:x", "memory", "1"
    )

    assert (
        cache.set_resolved("source:test", "screen.xml", template, generation) is False
    )
    assert backend.get(raw_key, "absent") == "absent"
    assert (
        cache.set_resolved(
            "source:test", "screen.xml", template, cache.generation("screen.xml")
        )
        is True
    )


@pytest.mark.parametrize("operation", ["get", "delete"])
@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_unconfirmable_publication_fails_closed_and_attempts_cleanup(
    operation, effect, bind_backend
):
    cache = TemplateCache(f"cleanup-{operation}-{effect}", alias="screens", ttl=10)
    backend = ClockBackend()
    bind_backend(backend)
    cache.generation("screen.xml")
    cache.invalidate("screen.xml")
    generation = cache.generation("screen.xml")
    raw_key, generation_key = arm_rotation(cache, backend, generation)
    fault_key = {"get": generation_key, "delete": raw_key}[operation]
    backend.faults[(operation, fault_key)] = effect
    template = ResolvedTemplate("screen.xml", "old", "memory:x", "memory", "old")

    if operation == "delete" and effect == "false":
        assert (
            cache.set_resolved("source:test", "screen.xml", template, generation)
            is False
        )
    else:
        with pytest.raises(SourceUnavailable, match="backend failure"):
            cache.set_resolved("source:test", "screen.xml", template, generation)
    backend.faults.clear()
    if operation == "delete":
        assert backend.get(raw_key, "absent") != "absent"
    else:
        assert backend.get(raw_key, "absent") == "absent"


@pytest.mark.parametrize("mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_obeys_policy_when_publication_confirmation_fails(mode, bind_backend):
    cache = TemplateCache(f"confirmation-{mode}", alias="screens")
    backend = ClockBackend()
    bind_backend(backend)
    generation = cache.generation("screen.xml")
    raw_key, generation_key = arm_rotation(cache, backend, generation)
    backend.faults[("get", generation_key)] = "exception"
    source = Source({"screen.xml": "authoritative"})

    if mode == "raise":
        with pytest.raises(SourceUnavailable, match="backend failure"):
            resolver(source, cache, mode).resolve("screen.xml")
    else:
        assert resolver(source, cache, mode).resolve("screen.xml").content == (
            "authoritative"
        )
    backend.faults.clear()
    assert backend.get(raw_key, "absent") == "absent"
