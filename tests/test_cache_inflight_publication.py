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
def test_inflight_writer_cannot_publish_into_reused_generation(old, transition):
    cache = TemplateCache(
        f"inflight-{old}-{transition}", alias="screens", ttl=10, negative_ttl=5
    )
    backend = ClockBackend()
    cache.backend = backend
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


@override_settings(CACHES=LOCMEM_CACHES)
def test_current_claim_is_permanent_and_retirement_starts_at_rotation():
    cache = TemplateCache("claim-lifecycle", alias="screens", ttl=10, negative_ttl=5)
    backend = ClockBackend()
    cache.backend = backend
    with patch("dj_hyperview.cache.secrets.token_hex", return_value="a" * 32):
        old = cache.generation("screen.xml")
    assert backend.expiry(cache._claim_key("screen.xml", old)) is None

    backend.advance(4)
    with patch("dj_hyperview.cache.secrets.token_hex", return_value="b" * 32):
        cache.invalidate("screen.xml")
    new = cache.generation("screen.xml")
    assert backend.expiry(cache._claim_key("screen.xml", old)) == 14
    assert backend.expiry(cache._claim_key("screen.xml", new)) is None


@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_failed_rotation_retires_its_unused_candidate(effect):
    cache = TemplateCache(f"failed-candidate-{effect}", alias="screens", ttl=10)
    backend = ClockBackend()
    cache.backend = backend
    current, candidate = cache.generation("screen.xml"), "f" * 32
    backend.faults[("set", cache._generation_key("screen.xml"))] = effect
    with patch.object(cache, "_candidate", return_value=candidate):
        with pytest.raises(SourceUnavailable, match="backend failure"):
            cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") == current
    assert backend.expiry(cache._claim_key("screen.xml", candidate)) == 15


def arm_rotation(cache, backend, generation):
    raw_key = cache.key(
        "source:test", "screen.xml", cache._resolved_revision(generation)
    )
    generation_key = cache.key("@generation", "screen.xml", "@token")
    successor = "f" * 32

    def rotate(key):
        if key == raw_key:
            backend._put(generation_key, successor, None)
            backend._put(cache._claim_key("screen.xml", successor), "claimed", None)

    backend.after_set = rotate
    return raw_key, generation_key


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_handles_rotation_between_raw_write_and_confirmation(old, mode):
    cache = TemplateCache(f"publication-{old}-{mode}", alias="screens", ttl=10)
    backend = ClockBackend()
    cache.backend = backend
    source = Source({"screen.xml": old})
    generation = cache.generation("screen.xml")
    raw_key, _ = arm_rotation(cache, backend, generation)

    if mode == "raise":
        with pytest.raises(SourceUnavailable, match="generation changed"):
            resolver(source, cache, mode).resolve("screen.xml")
    elif old is None:
        with pytest.raises(TemplateNotFound):
            resolver(source, cache, mode).resolve("screen.xml")
    else:
        assert resolver(source, cache, mode).resolve("screen.xml").content == "old"
    assert backend.get(raw_key, "absent") == "absent"


@pytest.mark.parametrize("operation", ["get", "claim", "delete"])
@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_unconfirmable_publication_fails_closed_and_attempts_cleanup(operation, effect):
    cache = TemplateCache(f"cleanup-{operation}-{effect}", alias="screens", ttl=10)
    backend = ClockBackend()
    cache.backend = backend
    generation = cache.generation("screen.xml")
    raw_key, generation_key = arm_rotation(cache, backend, generation)
    claim_key = cache._claim_key("screen.xml", generation)
    fault_key = {"get": generation_key, "claim": claim_key, "delete": raw_key}[
        operation
    ]
    backend.faults[("set" if operation == "claim" else operation, fault_key)] = effect
    template = ResolvedTemplate("screen.xml", "old", "memory:x", "memory", "old")

    with pytest.raises(SourceUnavailable, match="backend failure"):
        cache.set_resolved("source:test", "screen.xml", template, generation)
    backend.faults.clear()
    if operation == "delete":
        assert backend.get(raw_key, "absent") != "absent"
        assert backend.expiry(claim_key) == max(cache.ttl, cache.negative_ttl)
    else:
        assert backend.get(raw_key, "absent") == "absent"


@pytest.mark.parametrize("mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_obeys_policy_when_publication_confirmation_fails(mode):
    cache = TemplateCache(f"confirmation-{mode}", alias="screens")
    backend = ClockBackend()
    cache.backend = backend
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
