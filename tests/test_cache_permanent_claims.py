from threading import Event
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from tests.test_cache_generation import LOCMEM_CACHES, Source, launch
from tests.test_cache_inflight_publication import BlockingSource, ClockBackend, resolver

__test__ = False


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_successor_tombstone_blocks_replay_during_late_publication(old):
    namespace = f"successor-aba-{old}"
    backend = ClockBackend()
    stale = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
    active = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
    stale.backend = active.backend = backend
    root = active.generation("screen.xml")
    entered_claim, release_claim = Event(), Event()
    original_claim = stale._claim_generation

    with patch("dj_hyperview.cache.secrets.token_hex", return_value="b" * 32):
        successor = active._candidate("screen.xml", root)

    def delayed_claim(name, current):
        assert current == root
        entered_claim.set()
        assert release_claim.wait(timeout=3)
        return original_claim(name, current)

    stale._claim_generation = delayed_claim
    with patch.object(stale, "_candidate", return_value=successor):
        stale_thread, stale_outcome = launch(lambda: stale.invalidate("screen.xml"))
        assert entered_claim.wait(timeout=3)

        with patch.object(active, "_candidate", return_value=successor):
            active.invalidate("screen.xml")
        source = BlockingSource(old)
        writer_thread, writer_outcome = launch(
            lambda: resolver(source, active, "bypass").resolve("screen.xml")
        )
        assert source.started.wait(timeout=3)

        with patch("dj_hyperview.cache.secrets.token_hex", return_value="c" * 32):
            active.invalidate("screen.xml")
        replacement = active.generation("screen.xml")
        assert replacement != successor

        backend.advance(1000)
        raw_key = active.key(
            "source:test", "screen.xml", active._resolved_revision(successor)
        )
        raw_written, allow_confirmation = Event(), Event()

        def block_after_raw(key):
            if key == raw_key:
                raw_written.set()
                assert allow_confirmation.wait(timeout=3)

        backend.after_set = block_after_raw
        source.value = "new"
        source.release.set()
        assert raw_written.wait(timeout=3)
        release_claim.set()
        stale_thread.join(timeout=3)
        assert not stale_thread.is_alive()

        fresh = Source({"screen.xml": "new"})
        other = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
        other.backend = backend
        try:
            observed = resolver(fresh, other, "raise").resolve("screen.xml").content
        except TemplateNotFound:
            observed = "cached-negative-miss"
        finally:
            allow_confirmation.set()
            writer_thread.join(timeout=3)

    assert not writer_thread.is_alive()
    assert observed == "new"
    assert fresh.calls == ["screen.xml"]
    assert isinstance(stale_outcome.get("error"), SourceUnavailable)
    if old is None:
        assert isinstance(writer_outcome.get("error"), TemplateNotFound)
    else:
        assert writer_outcome["value"].content == old


@pytest.mark.parametrize("state", ["content", "negative-miss"])
@pytest.mark.parametrize("mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_ambiguous_raw_delete_keeps_tombstone_permanent(state, mode):
    cache = TemplateCache(
        f"delete-none-{state}", alias="screens", ttl=10, negative_ttl=5
    )
    backend = ClockBackend()
    cache.backend = backend
    cache.generation("screen.xml")
    cache.invalidate("screen.xml")
    predecessor = cache.generation("screen.xml")
    raw_key = cache.key(
        "source:test", "screen.xml", cache._resolved_revision(predecessor)
    )
    successor = "s" + "f" * 32

    def rotate_after_raw(key):
        if key == raw_key:
            backend._put(cache._generation_key("screen.xml"), successor, None)
            backend._put(
                cache._claim_key("screen.xml", successor),
                cache._claim_value("screen.xml", successor, "successor"),
                None,
            )

    backend.after_set = rotate_after_raw
    original_delete = backend.delete

    def ambiguous_delete(key):
        return None if key == raw_key else original_delete(key)

    source = Source({"screen.xml": "old" if state == "content" else None})
    with patch.object(backend, "delete", side_effect=ambiguous_delete):
        if mode == "raise":
            with pytest.raises(SourceUnavailable, match="backend failure"):
                resolver(source, cache, mode).resolve("screen.xml")
        elif state == "content":
            assert resolver(source, cache, mode).resolve("screen.xml").content == "old"
        else:
            with pytest.raises(TemplateNotFound):
                resolver(source, cache, mode).resolve("screen.xml")

    assert source.calls == ["screen.xml"]
    assert backend.get(raw_key, "absent") != "absent"
    assert backend.expiry(cache._claim_key("screen.xml", predecessor)) is None


@override_settings(CACHES=LOCMEM_CACHES)
def test_all_generation_candidates_remain_permanent_after_rotation():
    cache = TemplateCache("permanent-candidates", alias="screens", ttl=10)
    backend = ClockBackend()
    cache.backend = backend
    tokens = [cache.generation("screen.xml")]
    for entropy in ("b" * 32, "c" * 32):
        with patch("dj_hyperview.cache.secrets.token_hex", return_value=entropy):
            cache.invalidate("screen.xml")
        tokens.append(cache.generation("screen.xml"))

    backend.advance(10_000)
    assert [
        backend.expiry(cache._claim_key("screen.xml", token)) for token in tokens
    ] == [
        None,
        None,
        None,
    ]
