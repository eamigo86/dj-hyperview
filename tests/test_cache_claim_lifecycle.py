from threading import Barrier, Event
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.sources import ResolvedTemplate
from tests.test_cache_generation import LOCMEM_CACHES, launch
from tests.test_cache_inflight_publication import ClockBackend, arm_rotation


def successor_cache(namespace):
    cache = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
    backend = ClockBackend()
    cache.backend = backend
    cache.generation("screen.xml")
    cache.invalidate("screen.xml")
    return cache, backend, cache.generation("screen.xml")


@pytest.mark.parametrize("state", ["content", "negative-miss"])
@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_unconfirmable_write_keeps_current_successor_permanent(state, effect):
    cache, backend, current = successor_cache(f"uncertain-{state}-{effect}")
    claim_key = cache._claim_key("screen.xml", current)
    generation_key = cache._generation_key("screen.xml")
    assert backend.expiry(claim_key) is None
    backend.faults[("get", generation_key)] = effect
    template = ResolvedTemplate("screen.xml", "fresh", "memory:x", "memory", "r1")

    with pytest.raises(SourceUnavailable, match="backend failure"):
        if state == "content":
            cache.set_resolved("source:test", "screen.xml", template, current)
        else:
            cache.set_resolved_miss("source:test", "screen.xml", current)

    backend.faults.clear()
    assert backend.get(generation_key) == current
    assert backend.expiry(claim_key) is None
    backend.advance(16)
    assert cache.generation("screen.xml") == current
    cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") != current


@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_failed_raw_delete_keeps_replaced_successor_permanent(effect):
    cache, backend, current = successor_cache(f"delete-{effect}")
    raw_key, _ = arm_rotation(cache, backend, current)
    backend.faults[("delete", raw_key)] = effect
    template = ResolvedTemplate("screen.xml", "old", "memory:x", "memory", "old")

    with pytest.raises(SourceUnavailable, match="backend failure"):
        cache.set_resolved("source:test", "screen.xml", template, current)

    backend.faults.clear()
    assert backend.get(raw_key, "absent") != "absent"
    assert backend.expiry(cache._claim_key("screen.xml", current)) is None


@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_touch_failure_falls_back_to_promoting_current_claim(effect):
    cache, backend, current = successor_cache(f"touch-fallback-{effect}")
    claim_key = cache._claim_key("screen.xml", current)
    marker = backend.get(claim_key)
    backend._put(claim_key, marker, 3)
    backend.faults[("touch", claim_key)] = effect

    assert cache.generation("screen.xml") == current
    assert backend.expiry(claim_key) is None


@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_touch_and_set_failure_make_claim_promotion_fail_typed(effect):
    cache, backend, current = successor_cache(f"touch-set-{effect}")
    claim_key = cache._claim_key("screen.xml", current)
    backend.faults.update({("touch", claim_key): effect, ("set", claim_key): effect})

    with pytest.raises(SourceUnavailable, match="backend failure"):
        cache.generation("screen.xml")


@pytest.mark.parametrize("actual", ["root", "successor"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_current_generation_rejects_valid_marker_with_wrong_kind(actual):
    cache = TemplateCache(f"kind-current-{actual}", alias="screens")
    backend = ClockBackend()
    cache.backend = backend
    current = cache.generation("screen.xml")
    if actual == "successor":
        cache.invalidate("screen.xml")
        current = cache.generation("screen.xml")
    wrong = "successor" if actual == "root" else "root"
    backend._put(
        cache._claim_key("screen.xml", current),
        cache._claim_value("screen.xml", current, wrong),
        None,
    )

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        cache.generation("screen.xml")


@pytest.mark.parametrize("current", [None, "existing"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_candidate_collision_rejects_valid_marker_with_wrong_kind(current):
    cache = TemplateCache(f"kind-candidate-{current}", alias="screens")
    backend = ClockBackend()
    cache.backend = backend
    existing = cache.generation("screen.xml")
    predecessor = None if current is None else existing
    expected = "root" if predecessor is None else "successor"
    candidate = cache._candidate("screen.xml", predecessor)
    wrong = "successor" if expected == "root" else "root"
    backend._put(
        cache._claim_key("screen.xml", candidate),
        cache._claim_value("screen.xml", candidate, wrong),
        None,
    )

    with patch.object(cache, "_candidate", return_value=candidate):
        with pytest.raises(SourceUnavailable, match="invalid payload"):
            cache._claim_generation("screen.xml", predecessor)


@override_settings(CACHES=LOCMEM_CACHES)
def test_stale_rotation_retires_successor_seen_immediately_before_write():
    backend = ClockBackend()
    first = TemplateCache("stale-rotate", alias="screens", ttl=10)
    second = TemplateCache("stale-rotate", alias="screens", ttl=10)
    first.backend = second.backend = backend
    root = first.generation("screen.xml")
    both_claimed, first_finished = Barrier(2), Event()
    original_first, original_second = first._claim_generation, second._claim_generation

    def claim(original, wait_for_first=False):
        def coordinated(name, current):
            assert current == root
            candidate = original(name, current)
            both_claimed.wait(timeout=3)
            if wait_for_first:
                assert first_finished.wait(timeout=3)
            return candidate

        return coordinated

    first._claim_generation = claim(original_first)
    second._claim_generation = claim(original_second, True)
    first_candidate = first._candidate("screen.xml", root)
    second_candidate = second._candidate("screen.xml", root)

    def rotate_first():
        try:
            first.invalidate("screen.xml")
        finally:
            first_finished.set()

    with (
        patch.object(first, "_candidate", return_value=first_candidate),
        patch.object(second, "_candidate", return_value=second_candidate),
    ):
        threads = [
            launch(rotate_first),
            launch(lambda: second.invalidate("screen.xml")),
        ]
        for thread, outcome in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert outcome == {"value": None}

    assert second.generation("screen.xml") == second_candidate
    assert backend.expiry(first._claim_key("screen.xml", first_candidate)) == max(
        first.ttl, first.negative_ttl
    )
