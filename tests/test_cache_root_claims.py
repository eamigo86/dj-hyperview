import json
from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from tests.test_cache_generation import LOCMEM_CACHES, Source, launch
from tests.test_cache_inflight_publication import BlockingSource, ClockBackend


def resolver(source, cache):
    return TemplateResolver(
        [source], cache=cache, failure_mode="bypass", _source_ids=["source:test"]
    )


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("effect", ["false", "exception"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_root_claim_blocks_late_raw_after_both_cleanup_steps_fail(old, effect):
    namespace = f"root-cleanup-{old}-{effect}"
    cache = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
    backend = ClockBackend()
    cache.backend = backend
    source = BlockingSource(old)

    entropy = "a" * 32
    with patch("dj_hyperview.cache.secrets.token_hex", return_value=entropy):
        thread, outcome = launch(lambda: resolver(source, cache).resolve("screen.xml"))
        assert source.started.wait(timeout=3)
    root = cache.generation("screen.xml")
    claim_key = cache._claim_key("screen.xml", root)

    with patch("dj_hyperview.cache.secrets.token_hex", return_value="b" * 32):
        cache.invalidate("screen.xml")
    backend.advance(11)
    assert backend.expiry(claim_key) is None

    raw_key = cache.key("source:test", "screen.xml", cache._resolved_revision(root))
    backend.faults.update({("set", claim_key): effect, ("delete", raw_key): effect})
    source.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    if old is None:
        assert isinstance(outcome["error"], TemplateNotFound)
    else:
        assert outcome["value"].content == old
    assert backend.get(raw_key, "absent") != "absent"

    backend.faults.clear()
    assert backend.delete(cache._generation_key("screen.xml")) is True
    fresh = Source({"screen.xml": "new"})
    other = TemplateCache(namespace, alias="screens", ttl=10, negative_ttl=5)
    other.backend = backend
    with patch("dj_hyperview.cache.secrets.token_hex", return_value=entropy):
        assert resolver(fresh, other).resolve("screen.xml").content == "new"
    assert fresh.calls == ["screen.xml"]


@pytest.mark.parametrize("state", ["missing", "malformed", "get-error"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_current_generation_requires_an_exact_claim_marker(state):
    cache = TemplateCache(f"current-claim-{state}", alias="screens")
    backend = ClockBackend()
    cache.backend = backend
    generation = cache.generation("screen.xml")
    claim_key = cache._claim_key("screen.xml", generation)
    backend.delete(claim_key)
    if state == "malformed":
        backend._put(claim_key, '{"kind":"root"}', None)
    else:
        backend.faults[("add", claim_key)] = "false"
    if state == "get-error":
        backend.faults[("get", claim_key)] = "exception"

    with pytest.raises(SourceUnavailable):
        cache.generation("screen.xml")


@pytest.mark.parametrize("state", ["missing", "malformed", "get-error"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_candidate_add_false_requires_an_exact_claim_marker(state):
    cache = TemplateCache(f"candidate-claim-{state}", alias="screens")
    backend = ClockBackend()
    cache.backend = backend
    current = cache.generation("screen.xml")
    candidate, fallback = "s" + "e" * 32, "s" + "f" * 32
    claim_key = cache._claim_key("screen.xml", candidate)
    if state == "malformed":
        backend._put(claim_key, "untrusted", None)
    else:
        backend.faults[("add", claim_key)] = "false"
    if state == "get-error":
        backend.faults[("get", claim_key)] = "exception"

    with patch.object(cache, "_candidate", side_effect=[candidate, fallback]):
        with pytest.raises(SourceUnavailable):
            cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") == current


@override_settings(CACHES=LOCMEM_CACHES)
def test_valid_claim_collision_retries_and_markers_are_secret_safe():
    first = TemplateCache("marker-one", alias="screens")
    second = TemplateCache("marker-two", alias="screens")
    backend = ClockBackend()
    first.backend = backend
    first.generation("screen.xml")
    collision, successor = "s" + "e" * 32, "s" + "f" * 32
    marker = first._claim_value("screen.xml", collision, "successor")
    backend._put(first._claim_key("screen.xml", collision), marker, None)

    with patch.object(first, "_candidate", side_effect=[collision, successor]):
        first.invalidate("screen.xml")
    assert first.generation("screen.xml") == successor
    assert marker != second._claim_value("screen.xml", collision, "successor")
    envelope = json.loads(marker)
    assert envelope == {
        "version": 1,
        "kind": "successor",
        "identity": envelope["identity"],
    }
    assert "marker-one" not in marker and "screen.xml" not in marker
