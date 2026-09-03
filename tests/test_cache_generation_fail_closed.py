from unittest.mock import patch

import pytest
from django.test import override_settings

from dj_hyperview import invalidate_templates
from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate
from tests.test_cache_generation import LOCMEM_CACHES, Source, config, resolver

SHARED_CACHES = {
    alias: {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "generation-shared-alias",
    }
    for alias in ("publisher", "reader")
}


def token_sequence(*values):
    return iter(values)


@override_settings(CACHES=LOCMEM_CACHES)
def test_rotation_retries_claimed_candidates_without_namespace_reuse():
    cache = TemplateCache("claimed-rotation", alias="screens")
    old = cache.generation("screen.xml")
    new = "f" * 32
    with patch.object(cache, "_candidate", side_effect=token_sequence(old, old, new)):
        cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") == new


@override_settings(CACHES=LOCMEM_CACHES)
def test_generation_eviction_rejects_retained_historical_claim():
    source = Source({"screen.xml": "old"})
    current = resolver(source, "claimed-eviction")
    assert current.resolve("screen.xml").content == "old"
    old = current.cache.generation("screen.xml")
    source.values["screen.xml"] = "new"
    current.cache.backend.delete(
        current.cache.key("@generation", "screen.xml", "@token")
    )
    with patch.object(
        current.cache, "_candidate", side_effect=token_sequence(old, "e" * 32)
    ):
        assert resolver(source, "claimed-eviction").resolve("screen.xml").content == (
            "new"
        )


@override_settings(CACHES=LOCMEM_CACHES)
def test_candidate_exhaustion_raises_without_rotating():
    cache = TemplateCache("claim-exhaustion", alias="screens")
    old = cache.generation("screen.xml")
    with patch.object(cache, "_candidate", return_value=old):
        with pytest.raises(SourceUnavailable, match="backend failure"):
            cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") == old


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("failure", ["false", "exception"])
@override_settings(CACHES=SHARED_CACHES)
def test_bypass_invalidation_failure_never_reports_success(old, failure):
    namespace = f"barrier-{old}-{failure}"
    source = Source({"screen.xml": old})
    cache = TemplateCache(namespace, alias="publisher")
    publisher = TemplateResolver(
        [source], cache=cache, failure_mode="bypass", _source_ids=["source:test"]
    )
    if old is None:
        with pytest.raises(TemplateNotFound):
            publisher.resolve("screen.xml")
    else:
        assert publisher.resolve("screen.xml").content == "old"
    source.values["screen.xml"] = "new"
    failed = False if failure == "false" else RuntimeError("secret payload")
    effect = {"return_value": failed} if failure == "false" else {"side_effect": failed}
    hyperview = config(namespace, "bypass")
    hyperview["CACHE"]["ALIAS"] = "publisher"
    with (
        override_settings(HYPERVIEW=hyperview),
        patch.object(TemplateCache, "from_settings", return_value=cache),
        patch.object(cache.backend, "set", **effect),
        pytest.raises(SourceUnavailable, match="backend failure"),
    ):
        invalidate_templates("screen.xml")

    reader = TemplateResolver(
        [source],
        cache=TemplateCache(namespace, alias="reader"),
        failure_mode="bypass",
        _source_ids=["source:test"],
    )
    if old is None:
        with pytest.raises(TemplateNotFound):
            reader.resolve("screen.xml")
    else:
        assert reader.resolve("screen.xml").content == "old"


@override_settings(CACHES=LOCMEM_CACHES)
def test_partial_batch_failure_is_observable_and_retryable():
    cache = TemplateCache("partial-batch", alias="screens")
    error = SourceUnavailable("cache:screens", "backend failure")
    with (
        override_settings(HYPERVIEW=config("partial-batch", "bypass")),
        patch.object(TemplateCache, "from_settings", return_value=cache),
        patch.object(cache, "invalidate", side_effect=[None, error]) as rotate,
        pytest.raises(SourceUnavailable, match="backend failure"),
    ):
        invalidate_templates("one.xml", "two.xml")
    assert [call.args[0] for call in rotate.call_args_list] == ["one.xml", "two.xml"]

    with (
        override_settings(HYPERVIEW=config("partial-batch", "bypass")),
        patch.object(TemplateCache, "from_settings", return_value=cache),
        patch.object(cache, "invalidate") as retry,
    ):
        invalidate_templates("one.xml", "two.xml")
    assert [call.args[0] for call in retry.call_args_list] == ["one.xml", "two.xml"]


@pytest.mark.parametrize("operation", ["set", "set_miss", "resolved", "resolved_miss"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_false_raw_write_raises_source_unavailable(operation):
    cache = TemplateCache(f"raw-false-{operation}", alias="screens")
    template = ResolvedTemplate("screen.xml", "raw", "memory:x", "memory", "r1")
    calls = {
        "set": lambda: cache.set(template),
        "set_miss": lambda: cache.set_miss("memory", "screen.xml", "r1"),
        "resolved": lambda: cache.set_resolved("source:test", "screen.xml", template),
        "resolved_miss": lambda: cache.set_resolved_miss("source:test", "screen.xml"),
    }
    with patch.object(cache.backend, "set", return_value=False):
        with pytest.raises(SourceUnavailable, match="backend failure"):
            calls[operation]()


@pytest.mark.parametrize("old", ["old", None], ids=["content", "negative-miss"])
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_resolver_false_write_obeys_failure_mode(old, failure_mode):
    source = Source({"screen.xml": old})
    current = resolver(source, f"resolver-false-{old}-{failure_mode}", failure_mode)
    current.cache.generation("screen.xml")
    with patch.object(current.cache.backend, "set", return_value=False):
        if failure_mode == "raise":
            with pytest.raises(SourceUnavailable, match="backend failure"):
                current.resolve("screen.xml")
        elif old is None:
            with pytest.raises(TemplateNotFound):
                current.resolve("screen.xml")
        else:
            assert current.resolve("screen.xml").content == "old"
    assert source.calls == ["screen.xml"]


@override_settings(CACHES=LOCMEM_CACHES)
def test_documented_none_write_result_remains_successful():
    cache = TemplateCache("none-success", alias="screens")
    old = cache.generation("screen.xml")
    original = cache.backend.set

    def successful_none(*args, **kwargs):
        original(*args, **kwargs)
        return None

    with patch.object(cache.backend, "set", side_effect=successful_none):
        cache.invalidate("screen.xml")
    assert cache.generation("screen.xml") != old
