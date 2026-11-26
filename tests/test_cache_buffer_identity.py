"""Buffer-typed option values cannot produce a diverging source fingerprint."""

from typing import Never

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


def fingerprint(value: object) -> str | None:
    """Fingerprint a source whose only option is the given value.

    Args:
        value: Option value to fingerprint.

    Returns:
        The stable fingerprint, or None when the value cannot be represented.
    """
    return _source_fingerprint(0, BACKEND, {"value": value})


def test_memoryview_mapping_key_disables_fingerprint_instead_of_diverging() -> None:
    """A memoryview mapping key disables the fingerprint rather than diverging."""
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
def test_every_memoryview_layout_is_unrepresentable(value: memoryview) -> None:
    """Every memoryview layout is unrepresentable in a fingerprint.

    Args:
        value: Memoryview with the layout under test.
    """
    assert fingerprint(value) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param((True,), (1.0,), id="tuple-numeric"),
    ],
)
def test_other_supported_equal_mapping_keys_remain_canonical(
    left: tuple[object, ...], right: tuple[object, ...]
) -> None:
    """Equal keys that are not buffers still give the same fingerprint.

    Args:
        left: Mapping key that equals the right one.
        right: Mapping key that equals the left one.
    """
    assert {left: "value"} == {right: "value"}
    assert fingerprint({left: "value"}) == fingerprint({right: "value"})


def test_mutable_bytearray_value_is_unrepresentable() -> None:
    """A mutable bytearray value is unrepresentable in a fingerprint."""
    mutable = bytearray(b"same-key")

    assert fingerprint(mutable) is None
    with pytest.raises(TypeError):
        hash(mutable)


def test_buffer_mapping_order_is_canonical_and_values_affect_identity() -> None:
    """Mapping order is ignored, while changed values change the fingerprint."""
    items = [(b"bytes", "binary"), ("bytes", "text"), (1, "number")]
    left, right = dict(items), dict(reversed(items))

    assert fingerprint(left) == fingerprint(right)
    assert fingerprint(left) != fingerprint({**right, b"bytes": "changed"})


class BufferOptionSource:
    """Source that counts resolutions and keeps one option value.

    Args:
        value: Option value kept by the source.
    """

    calls = 0

    def __init__(self, value: object) -> None:
        """Keep the option value."""
        self.value = value

    def resolve(self, name: str) -> ResolvedTemplate:
        """Count the resolution and return a fixed raw template.

        Args:
            name: Template name to resolve.

        Returns:
            A resolved template with fixed content.
        """
        type(self).calls += 1
        return ResolvedTemplate(name, "raw", "buffer:x", "buffer", "r1")


@override_settings(CACHES=CACHES)
def test_memoryview_option_disables_cache_for_real_resolver() -> None:
    """A memoryview option disables caching, so every resolve reaches the source."""
    source = {"BACKEND": BACKEND, "OPTIONS": {"value": memoryview(b"same-key")}}
    hyperview = {"SOURCES": [source], "CACHE": CACHE}
    BufferOptionSource.calls = 0

    with override_settings(HYPERVIEW=hyperview):
        resolver = TemplateResolver.from_settings()
        resolver.resolve("screen.xml")
        resolver.resolve("screen.xml")

    assert BufferOptionSource.calls == 2


class FailingBufferSource:
    """Source that keeps one option value and always reports itself offline.

    Args:
        value: Option value kept by the source.
    """

    def __init__(self, value: object) -> None:
        """Keep the option value."""
        self.value = value

    def resolve(self, name: str) -> Never:
        """Fail as an unavailable source.

        Args:
            name: Template name to resolve.

        Raises:
            SourceUnavailable: Always, with the reason offline.
        """
        raise SourceUnavailable("real-source", "offline")


@override_settings(CACHES=CACHES)
def test_uncached_memoryview_source_failure_is_not_hidden() -> None:
    """An uncached source failure reaches the caller unchanged."""
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
