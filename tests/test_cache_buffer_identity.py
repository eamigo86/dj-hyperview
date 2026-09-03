import pytest
from django.test import override_settings

from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.resolver import TemplateResolver, _source_fingerprint
from dj_hyperview.sources import ResolvedTemplate

BACKEND = f"{__name__}.BufferOptionSource"
CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "buffer-fingerprint",
    }
}
CACHE = {"ALIAS": "screens", "NAMESPACE": "buffer-fingerprint"}


def fingerprint(value):
    return _source_fingerprint(0, BACKEND, {"value": value})


def test_memoryview_mapping_key_disables_fingerprint_instead_of_diverging():
    raw = b"same-key"
    bytes_mapping = {raw: "value"}
    view_mapping = {memoryview(raw): "value"}

    assert bytes_mapping == view_mapping
    assert fingerprint(bytes_mapping) is not None
    assert fingerprint(view_mapping) is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(memoryview(b"abcd"), id="readonly-contiguous"),
        pytest.param(memoryview(b"abcd")[::2], id="readonly-noncontiguous"),
        pytest.param(memoryview(b"abcd").cast("H"), id="cast-format"),
        pytest.param(memoryview(bytearray(b"abcd")), id="writable"),
    ],
)
def test_every_memoryview_layout_is_unrepresentable(value):
    assert fingerprint(value) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param((True,), (1.0,), id="tuple-numeric"),
        pytest.param(range(0, 3, 2), range(0, 4, 2), id="equal-range"),
    ],
)
def test_other_supported_equal_mapping_keys_remain_canonical(left, right):
    assert {left: "value"} == {right: "value"}
    assert fingerprint({left: "value"}) == fingerprint({right: "value"})


def test_bytearray_value_is_deterministic_but_distinct_from_immutable_bytes():
    mutable = bytearray(b"same-key")

    assert fingerprint(mutable) == fingerprint(bytearray(mutable))
    assert fingerprint(mutable) != fingerprint(bytes(mutable))
    with pytest.raises(TypeError):
        hash(mutable)


def test_buffer_mapping_order_is_canonical_and_values_affect_identity():
    items = [(b"bytes", "binary"), ("bytes", "text"), (1, "number")]
    left, right = dict(items), dict(reversed(items))

    assert fingerprint(left) == fingerprint(right)
    assert fingerprint(left) != fingerprint({**right, b"bytes": "changed"})


class BufferOptionSource:
    calls = 0

    def __init__(self, value):
        self.value = value

    def resolve(self, name):
        type(self).calls += 1
        return ResolvedTemplate(name, "raw", "buffer:x", "buffer", "r1")


@override_settings(CACHES=CACHES)
def test_memoryview_option_disables_cache_for_real_resolver():
    source = {"BACKEND": BACKEND, "OPTIONS": {"value": memoryview(b"same-key")}}
    hyperview = {"SOURCES": [source], "CACHE": CACHE}
    BufferOptionSource.calls = 0

    with override_settings(HYPERVIEW=hyperview):
        resolver = TemplateResolver.from_settings()
        resolver.resolve("screen.xml")
        resolver.resolve("screen.xml")

    assert BufferOptionSource.calls == 2


class FailingBufferSource:
    def __init__(self, value):
        self.value = value

    def resolve(self, name):
        raise SourceUnavailable("real-source", "offline")


@override_settings(CACHES=CACHES)
def test_uncached_memoryview_source_failure_is_not_hidden():
    source = {
        "BACKEND": f"{__name__}.FailingBufferSource",
        "OPTIONS": {"value": memoryview(b"same-key")},
    }
    hyperview = {"SOURCES": [source], "CACHE": CACHE}

    with override_settings(HYPERVIEW=hyperview):
        with pytest.raises(SourceUnavailable) as captured:
            TemplateResolver.from_settings().resolve("screen.xml")

    assert captured.value.source == "real-source"
    assert captured.value.reason == "offline"
