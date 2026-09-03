from array import array
from collections import deque
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum, IntEnum
from fractions import Fraction
from pathlib import PurePosixPath

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


def fingerprint(value):
    return _source_fingerprint(0, BACKEND, {"value": value})


def test_array_typecodes_are_unrepresentable_instead_of_colliding():
    byte_values = array("B", [1, 2])
    wide_values = array("H", [1, 2])

    assert byte_values.typecode != wide_values.typecode
    assert fingerprint(byte_values) is None
    assert fingerprint(wide_values) is None


class ArrayTypeSource:
    def __init__(self, value):
        self.value = value

    def resolve(self, name):
        content = self.value.typecode
        return ResolvedTemplate(name, content, "array:x", "array", content)


@override_settings(CACHES=CACHES)
def test_array_typecode_change_cannot_return_stale_cached_content():
    def configured(value):
        source = {"BACKEND": BACKEND, "OPTIONS": {"value": value}}
        return {"SOURCES": [source], "CACHE": CACHE}

    with override_settings(HYPERVIEW=configured(array("B", [1, 2]))):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "B"
    with override_settings(HYPERVIEW=configured(array("H", [1, 2]))):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert resolved.content == "H"


class CustomSequence(Sequence):
    def __getitem__(self, index):
        return (1, 2)[index]

    def __len__(self):
        return 2


class CustomMapping(Mapping):
    def __getitem__(self, key):
        return {"key": "value"}[key]

    def __iter__(self):
        return iter(("key",))

    def __len__(self):
        return 1


@pytest.mark.parametrize("value", [CustomSequence(), CustomMapping()])
def test_abstract_container_implementations_are_not_admitted(value):
    assert fingerprint(value) is None


class StringSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


class IntegerSubclass(int):
    pass


class FloatSubclass(float):
    pass


class ListSubclass(list):
    pass


class TupleSubclass(tuple):
    pass


class DictSubclass(dict):
    pass


class PathSubclass(PurePosixPath):
    pass


class ValueEnum(Enum):
    ITEM = "item"


class NumberEnum(IntEnum):
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
def test_unsupported_values_fail_closed_at_any_nesting_depth(value):
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
    specialized, builtin
):
    assert {specialized: "value"} == {builtin: "value"}
    assert fingerprint({specialized: "value"}) is None
    assert fingerprint({builtin: "value"}) is not None


class TrackingCache(BaseCache):
    values = {}
    get_calls = 0
    set_calls = 0

    def __init__(self, location, params):
        super().__init__(params)

    def get(self, key, default=None, version=None):
        type(self).get_calls += 1
        return type(self).values.get(key, default)

    def set(self, key, value, timeout=None, version=None):
        type(self).set_calls += 1
        type(self).values[key] = value
        return True

    def add(self, key, value, timeout=None, version=None):
        if key in type(self).values:
            return False
        type(self).values[key] = value
        return True

    @classmethod
    def reset(cls):
        cls.values = {}
        cls.get_calls = cls.set_calls = 0


TRACKED_CACHES = {
    "screens": {"BACKEND": f"{__name__}.TrackingCache", "LOCATION": "domain"}
}


class OptionalSource:
    calls = {}

    def __init__(self, label, value, content=None, secret=None):
        self.label, self.value = label, value
        self.content, self.secret = content, secret

    def resolve(self, name):
        type(self).calls[self.label] = type(self).calls.get(self.label, 0) + 1
        if self.content is None:
            return None
        return ResolvedTemplate(name, self.content, self.label, self.label, "r1")


@override_settings(CACHES=TRACKED_CACHES)
def test_only_unsupported_source_bypasses_cache_and_leaks_no_options():
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
    assert (TrackingCache.get_calls, TrackingCache.set_calls) == (5, 1)
    assert "never-cache-this" not in str(TrackingCache.values)


class FailingSource:
    def __init__(self, value):
        self.value = value

    def resolve(self, name):
        raise SourceUnavailable("actual-source", "offline")


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=TRACKED_CACHES)
def test_unsupported_source_preserves_real_failure_without_cache_io(failure_mode):
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
