"""Source fingerprints admit only a closed domain of option values."""

from array import array
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from enum import Enum, IntEnum
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Any, Never

import pytest
from django.core.cache.backends.base import BaseCache
from django.test import override_settings

from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.resolver import TemplateResolver, _source_fingerprint
from dj_hyperview.sources import ResolvedTemplate

BACKEND = f"{__name__}.ArrayTypeSource"
CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "closed-fingerprint-domain",
    }
}
CACHE = {"ALIAS": "screens", "NAMESPACE": "closed-fingerprint-domain"}


def fingerprint(value: object) -> str | None:
    """Fingerprint a source whose only option is the given value.

    Args:
        value: Option value to fingerprint.

    Returns:
        The stable fingerprint, or None when the value is outside the domain.
    """
    return _source_fingerprint(0, BACKEND, {"value": value})


def test_array_typecodes_are_unrepresentable_instead_of_colliding() -> None:
    """Arrays with different typecodes are unrepresentable rather than colliding."""
    byte_values = array("B", [1, 2])
    wide_values = array("H", [1, 2])

    assert byte_values.typecode != wide_values.typecode
    assert fingerprint(byte_values) is None
    assert fingerprint(wide_values) is None


class ArrayTypeSource:
    """Source whose content is the typecode of its array option.

    Args:
        value: Array whose typecode becomes the template content.
    """

    def __init__(self, value: array) -> None:
        """Keep the array option."""
        self.value = value

    def resolve(self, name: str) -> ResolvedTemplate:
        """Resolve to a template whose content is the array typecode.

        Args:
            name: Template name to resolve.

        Returns:
            A resolved template carrying the typecode as content and revision.
        """
        content = self.value.typecode
        return ResolvedTemplate(name, content, "array:x", "array", content)


@override_settings(CACHES=CACHES)
def test_array_typecode_change_cannot_return_stale_cached_content() -> None:
    """Changing the array typecode never returns stale cached content."""

    def configured(value: array) -> dict[str, object]:
        source = {"BACKEND": BACKEND, "OPTIONS": {"value": value}}
        return {"SOURCES": [source], "CACHE": CACHE}

    with override_settings(HYPERVIEW=configured(array("B", [1, 2]))):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "B"
    with override_settings(HYPERVIEW=configured(array("H", [1, 2]))):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert resolved.content == "H"


class CustomSequence(Sequence):
    """Minimal sequence implementation that holds the values 1 and 2."""

    def __getitem__(self, index: int) -> int:
        """Return an item of the fixed pair."""
        return (1, 2)[index]

    def __len__(self) -> int:
        """Return the number of items."""
        return 2


class CustomMapping(Mapping):
    """Minimal mapping implementation with the single entry key to value."""

    def __getitem__(self, key: str) -> str:
        """Return the value of the only key."""
        return {"key": "value"}[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the only key."""
        return iter(("key",))

    def __len__(self) -> int:
        """Return the number of entries."""
        return 1


@pytest.mark.parametrize("value", [CustomSequence(), CustomMapping()])
def test_abstract_container_implementations_are_not_admitted(value: object) -> None:
    """Custom sequence and mapping implementations are not admitted.

    Args:
        value: Custom container implementation.
    """
    assert fingerprint(value) is None


class StringSubclass(str):
    """String subclass that must not fingerprint like a string."""

    pass


class BytesSubclass(bytes):
    """Bytes subclass that must not fingerprint like bytes."""

    pass


class IntegerSubclass(int):
    """Integer subclass that must not fingerprint like an integer."""

    pass


class FloatSubclass(float):
    """Float subclass that must not fingerprint like a float."""

    pass


class ListSubclass(list):
    """List subclass that must not fingerprint like a list."""

    pass


class TupleSubclass(tuple):
    """Tuple subclass that must not fingerprint like a tuple."""

    pass


class DictSubclass(dict):
    """Dictionary subclass that must not fingerprint like a dictionary."""

    pass


class PathSubclass(PurePosixPath):
    """Pure path subclass that must not fingerprint like a path."""

    pass


class ValueEnum(Enum):
    """Enumeration with one string member."""

    ITEM = "item"


class NumberEnum(IntEnum):
    """Integer enumeration with one member."""

    ONE = 1


@pytest.mark.parametrize(
    "value",
    [
        array("B", [1, 2]),
        bytearray(b"bytes"),
        deque((1, 2)),
        range(2),
        CustomSequence(),
        CustomMapping(),
        ListSubclass((1, 2)),
        DictSubclass(key="value"),
        Decimal("1"),
        Fraction(1, 2),
        ValueEnum.ITEM,
        NumberEnum.ONE,
    ],
)
def test_unsupported_values_fail_closed_at_any_nesting_depth(value: object) -> None:
    """Unsupported values are unrepresentable alone and nested.

    Args:
        value: Value outside the supported domain.
    """
    assert fingerprint(value) is None
    assert fingerprint({"nested": [value]}) is None


@pytest.mark.parametrize(
    ("specialized", "builtin"),
    [
        (StringSubclass("key"), "key"),
        (BytesSubclass(b"key"), b"key"),
        (IntegerSubclass(1), 1),
        (FloatSubclass(1.5), 1.5),
        (TupleSubclass((1,)), (1,)),
        (PathSubclass("screen.xml"), PurePosixPath("screen.xml")),
    ],
)
def test_equal_subclass_mapping_keys_cannot_form_distinct_identities(
    specialized: object, builtin: object
) -> None:
    """A subclass key equal to a builtin key disables the fingerprint.

    Args:
        specialized: Subclass instance used as a mapping key.
        builtin: Builtin value that equals the subclass instance.
    """
    assert {specialized: "value"} == {builtin: "value"}
    assert fingerprint({specialized: "value"}) is None
    assert fingerprint({builtin: "value"}) is not None


class TrackingCache(BaseCache):
    """In-memory cache backend that counts reads and writes in class state.

    Args:
        location: Cache location, unused.
        params: Backend parameters passed to the base cache.
    """

    values = {}
    get_calls = 0
    set_calls = 0

    def __init__(self, location: str, params: dict[str, Any]) -> None:
        """Initialize the base cache with the parameters."""
        super().__init__(params)

    def get(
        self, key: str, default: object = None, version: int | None = None
    ) -> object:
        """Count the read and return the stored value.

        Args:
            key: Cache key to read.
            default: Value returned when the key is absent.
            version: Key version, unused.

        Returns:
            The stored value, or the default.
        """
        type(self).get_calls += 1
        return type(self).values.get(key, default)

    def set(
        self,
        key: str,
        value: object,
        timeout: int | None = None,
        version: int | None = None,
    ) -> bool:
        """Count the write and store the value.

        Args:
            key: Cache key to write.
            value: Value to store.
            timeout: Expiry, unused.
            version: Key version, unused.

        Returns:
            True, always.
        """
        type(self).set_calls += 1
        type(self).values[key] = value
        return True

    def add(
        self,
        key: str,
        value: object,
        timeout: int | None = None,
        version: int | None = None,
    ) -> bool:
        """Store the value only when the key is absent.

        Args:
            key: Cache key to write.
            value: Value to store.
            timeout: Expiry, unused.
            version: Key version, unused.

        Returns:
            True when the value was stored, False when the key existed.
        """
        if key in type(self).values:
            return False
        type(self).values[key] = value
        return True

    @classmethod
    def reset(cls) -> None:
        """Forget the stored values and zero the counters."""
        cls.values = {}
        cls.get_calls = cls.set_calls = 0


TRACKED_CACHES = {
    "screens": {"BACKEND": f"{__name__}.TrackingCache", "LOCATION": "domain"}
}


class OptionalSource:
    """Cacheable source that counts calls and may resolve nothing.

    Args:
        label: Name of the source, also its origin.
        value: Option value that influences the fingerprint.
        content: Template text to serve, or None to resolve nothing.
        secret: Option that must never reach the cache.
    """

    cacheable = True
    calls = {}

    def __init__(
        self,
        label: str,
        value: object,
        content: str | None = None,
        secret: str | None = None,
    ) -> None:
        """Keep the label, option value, content and secret."""
        self.label, self.value = label, value
        self.content, self.secret = content, secret

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Count the call and serve the content when there is some.

        Args:
            name: Template name to resolve.

        Returns:
            The resolved template, or None when the source has no content.
        """
        type(self).calls[self.label] = type(self).calls.get(self.label, 0) + 1
        if self.content is None:
            return None
        return ResolvedTemplate(name, self.content, self.label, self.label, "r1")


@override_settings(CACHES=TRACKED_CACHES)
def test_only_unsupported_source_bypasses_cache_and_leaks_no_options() -> None:
    """Only the unsupported source skips the cache and no option leaks into it."""
    backend = f"{__name__}.OptionalSource"
    unsupported = {
        "BACKEND": backend,
        "OPTIONS": {
            "label": "unsupported",
            "value": array("B", [1, 2]),
            "secret": "never-cache-this",
        },
    }
    stable = {
        "BACKEND": backend,
        "OPTIONS": {"label": "stable", "value": [1, 2], "content": "winner"},
    }
    TrackingCache.reset()
    OptionalSource.calls = {}

    with override_settings(
        HYPERVIEW={"SOURCES": [unsupported, stable], "CACHE": CACHE}
    ):
        resolver = TemplateResolver.from_settings()
        assert resolver.resolve("screen.xml").content == "winner"
        assert resolver.resolve("screen.xml").content == "winner"

    assert OptionalSource.calls == {"unsupported": 2, "stable": 1}
    assert (TrackingCache.get_calls, TrackingCache.set_calls) == (4, 1)
    assert "never-cache-this" not in str(TrackingCache.values)


class FailingSource:
    """Source that always reports itself offline.

    Args:
        value: Option value that influences the fingerprint.
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
        raise SourceUnavailable("actual-source", "offline")


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=TRACKED_CACHES)
def test_unsupported_source_preserves_real_failure_without_cache_io(
    failure_mode: str,
) -> None:
    """An unsupported source keeps its real failure and never touches the cache.

    Args:
        failure_mode: Cache failure mode configured for the resolver.
    """
    source = {
        "BACKEND": f"{__name__}.FailingSource",
        "OPTIONS": {"value": array("H", [1, 2])},
    }
    cache = {**CACHE, "FAILURE_MODE": failure_mode}
    TrackingCache.reset()

    with override_settings(HYPERVIEW={"SOURCES": [source], "CACHE": cache}):
        with pytest.raises(SourceUnavailable) as captured:
            TemplateResolver.from_settings().resolve("screen.xml")

    assert (captured.value.source, captured.value.reason) == (
        "actual-source",
        "offline",
    )
    assert (TrackingCache.get_calls, TrackingCache.set_calls) == (0, 0)
