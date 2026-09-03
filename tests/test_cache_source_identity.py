from pathlib import Path

import pytest
from django.core.cache.backends.base import BaseCache
from django.test import override_settings

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import render_template
from dj_hyperview.resolver import TemplateResolver

LOCMEM = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-fingerprint",
    }
}
CACHE = {"ALIAS": "screens", "NAMESPACE": "source-fingerprint"}
STUB = "tests.stubs.TemplateSource"
FILESYSTEM = "dj_hyperview.sources.FileSystemSource"


def configured(*sources, template_dirs=()):
    return {"TEMPLATE_DIRS": template_dirs, "SOURCES": sources, "CACHE": CACHE}


def stub(content, **extra):
    return {"BACKEND": STUB, "OPTIONS": {"content": content, **extra}}


@override_settings(CACHES=LOCMEM)
def test_changed_source_options_do_not_reuse_prior_content():
    with override_settings(HYPERVIEW=configured(stub("first", revision="one"))):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
    with override_settings(HYPERVIEW=configured(stub("second", revision="two"))):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")
        assert resolved.content == "second"


@override_settings(CACHES=LOCMEM)
def test_changed_implicit_template_dirs_do_not_reuse_prior_content(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "screen.xml").write_text("first")
    (second / "screen.xml").write_text("second")
    source = {"BACKEND": FILESYSTEM}

    with override_settings(HYPERVIEW=configured(source, template_dirs=[first])):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
    with override_settings(HYPERVIEW=configured(source, template_dirs=[second])):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")
        assert resolved.content == "second"


@override_settings(CACHES=LOCMEM)
def test_cache_active_engine_preserves_include_and_extends_resolution(tmp_path):
    (tmp_path / "layout.xml").write_text("<view>{% block body %}{% endblock %}</view>")
    (tmp_path / "part.xml").write_text("<text>{{ value }}</text>")
    (tmp_path / "screen.xml").write_text(
        '{% extends "layout.xml" %}'
        '{% block body %}{% include "part.xml" %}{% endblock %}'
    )
    source = {"BACKEND": FILESYSTEM}

    with override_settings(HYPERVIEW=configured(source, template_dirs=[tmp_path])):
        assert render_template("screen.xml", {"value": "A & B"}) == (
            "<view><text>A &amp; B</text></view>"
        )


@override_settings(CACHES=LOCMEM)
def test_reordered_repeated_backends_keep_entries_separate():
    first, second = stub("first"), stub("second")
    with override_settings(HYPERVIEW=configured(first, second)):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
    with override_settings(HYPERVIEW=configured(second, first)):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")
        assert resolved.content == "second"


def fingerprint_callable():
    return None


def test_fingerprint_is_canonical_for_supported_nested_configuration(tmp_path):
    from dj_hyperview.resolver import _source_fingerprint

    left = {
        "secret": "contraseña-🦊",
        "nested": {"path": Path(tmp_path), "items": [1, True, None]},
        "callable": fingerprint_callable,
        "dotted": "tests.stubs.TemplateSource",
    }
    right = {
        "dotted": left["dotted"],
        "callable": left["callable"],
        "nested": {"items": [1, True, None], "path": Path(tmp_path)},
        "secret": left["secret"],
    }

    first = _source_fingerprint(0, STUB, left)
    second = _source_fingerprint(0, STUB, right)

    assert first == second
    assert first.isascii() and len(first) == 71
    assert "contraseña" not in first
    assert _source_fingerprint(1, STUB, left) != first


class WrongCallableIdentity:
    __module__ = __name__
    __qualname__ = "fingerprint_callable"

    def __call__(self):
        return None


def test_fingerprint_handles_binary_float_and_unstable_callable_values():
    from dj_hyperview.resolver import _source_fingerprint

    assert _source_fingerprint(0, STUB, {"values": [b"raw", 1.5]})
    assert _source_fingerprint(0, STUB, {"value": float("inf")}) is None
    assert _source_fingerprint(0, STUB, {"value": lambda: None}) is None
    assert _source_fingerprint(0, STUB, {"value": WrongCallableIdentity()}) is None


class OpaqueSource:
    __slots__ = ("calls",)

    def __init__(self):
        self.calls = 0

    def resolve(self, name):
        self.calls += 1
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(name, "raw", "opaque:x", "opaque", "r1")


@override_settings(CACHES=LOCMEM)
def test_unfingerprintable_manual_source_is_not_cached():
    from dj_hyperview.cache import TemplateCache

    source = OpaqueSource()
    resolver = TemplateResolver(
        [source], cache=TemplateCache("opaque", alias="screens")
    )

    resolver.resolve("screen.xml")
    resolver.resolve("screen.xml")

    assert source.calls == 2


@pytest.mark.parametrize(
    "kwargs", [{"failure_mode": "ignore"}, {"_source_ids": ["one", "two"]}]
)
def test_resolver_rejects_invalid_cache_wiring(kwargs):
    with pytest.raises(ValueError, match="Cache failure mode|cache identity"):
        TemplateResolver([OpaqueSource()], **kwargs)


class ConfiguredOpaqueSource:
    calls = 0

    def __init__(self, content, opaque):
        del opaque
        self.content = content

    def resolve(self, name):
        type(self).calls += 1
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(name, self.content, "opaque:x", "opaque", "r1")


@override_settings(CACHES=LOCMEM)
def test_unfingerprintable_configured_options_disable_that_source_cache():
    source = {
        "BACKEND": f"{__name__}.ConfiguredOpaqueSource",
        "OPTIONS": {"content": "raw", "opaque": object()},
    }
    ConfiguredOpaqueSource.calls = 0
    with override_settings(HYPERVIEW=configured(source)):
        resolver = TemplateResolver.from_settings()
        resolver.resolve("screen.xml")
        resolver.resolve("screen.xml")

    assert ConfiguredOpaqueSource.calls == 2


class CountingInitCache(BaseCache):
    constructions = 0

    def __init__(self, location, params):
        type(self).constructions += 1
        super().__init__(params)

    def get(self, key, default=None, version=None):
        raise AssertionError("disabled cache was read")

    def set(self, key, value, timeout=None, version=None):
        raise AssertionError("disabled cache was written")


@pytest.mark.parametrize("cache_setting", [pytest.param(None, id="absent"), {}])
def test_disabled_cache_never_initializes_cache_handler(cache_setting):
    hyperview = {"SOURCES": [stub("<view />")]}
    if cache_setting is not None:
        hyperview["CACHE"] = cache_setting
    caches = {"default": {"BACKEND": f"{__name__}.CountingInitCache"}}
    CountingInitCache.constructions = 0

    with override_settings(CACHES=caches, HYPERVIEW=hyperview):
        get_settings()
        assert TemplateResolver.from_settings().resolve("screen.xml").content
        assert render_template("screen.xml") == "<view />"

    assert CountingInitCache.constructions == 0
