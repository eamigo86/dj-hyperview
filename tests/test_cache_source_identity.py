"""Cached entries are bound to the identity of the source that produced them."""

from collections.abc import Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Never

import pytest
from django.core.cache.backends.base import BaseCache
from django.test import override_settings

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import render_template
from dj_hyperview.resolver import TemplateResolver

if TYPE_CHECKING:
    from dj_hyperview.sources import ResolvedTemplate

LOCMEM = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-fingerprint",
    }
}
CACHE = {"ALIAS": "screens", "NAMESPACE": "source-fingerprint"}
STUB = "tests.stubs.CacheableTemplateSource"
FILESYSTEM = "dj_hyperview.sources.FileSystemSource"


def configured(
    *sources: dict[str, object], template_dirs: Sequence[Path] = ()
) -> dict[str, object]:
    """Build the HYPERVIEW settings for a list of sources and a cache.

    Args:
        *sources: Source configurations in resolution order.
        template_dirs: Template directories configured for the filesystem source.

    Returns:
        The HYPERVIEW settings mapping.
    """
    return {"TEMPLATE_DIRS": template_dirs, "SOURCES": sources, "CACHE": CACHE}


def stub(content: str, **extra: str) -> dict[str, object]:
    """Build the configuration of a cacheable stub source.

    Args:
        content: Template text served by the stub.
        **extra: Further stub options such as the revision.

    Returns:
        The source configuration with the stub backend and options.
    """
    return {"BACKEND": STUB, "OPTIONS": {"content": content, **extra}}


@override_settings(CACHES=LOCMEM)
def test_changed_source_options_do_not_reuse_prior_content() -> None:
    """Changing source options never reuses content cached under the old ones."""
    with override_settings(HYPERVIEW=configured(stub("first", revision="one"))):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
    with override_settings(HYPERVIEW=configured(stub("second", revision="two"))):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")
        assert resolved.content == "second"


@override_settings(CACHES=LOCMEM)
def test_changed_implicit_template_dirs_do_not_reuse_prior_content(
    tmp_path: Path,
) -> None:
    """Changing the implicit template directories never reuses cached content.

    Args:
        tmp_path: Temporary directory that holds both template directories.
    """
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
def test_cache_active_engine_preserves_include_and_extends_resolution(
    tmp_path: Path,
) -> None:
    """With the cache active, extends and include still resolve through the engine.

    Args:
        tmp_path: Temporary directory that holds the layout, part and screen.
    """
    (tmp_path / "layout.xml").write_text(
        '<view xmlns="https://hyperview.org/hyperview">'
        "{% block body %}{% endblock %}</view>"
    )
    (tmp_path / "part.xml").write_text("<text>{{ value }}</text>")
    (tmp_path / "screen.xml").write_text(
        '{% extends "layout.xml" %}'
        '{% block body %}{% include "part.xml" %}{% endblock %}'
    )
    source = {"BACKEND": FILESYSTEM}

    with override_settings(HYPERVIEW=configured(source, template_dirs=[tmp_path])):
        assert render_template("screen.xml", {"value": "A & B"}) == (
            '<view xmlns="https://hyperview.org/hyperview">'
            "<text>A &amp; B</text></view>"
        )


@override_settings(CACHES=LOCMEM)
def test_reordered_repeated_backends_keep_entries_separate() -> None:
    """Reordering repeated backends keeps their cached entries separate."""
    first, second = stub("first"), stub("second")
    with override_settings(HYPERVIEW=configured(first, second)):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
    with override_settings(HYPERVIEW=configured(second, first)):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")
        assert resolved.content == "second"


def fingerprint_callable() -> None:
    """Provide a callable with a stable import identity."""
    return None


def fingerprint(value: object) -> str | None:
    """Fingerprint a stub source whose only option is the given value.

    Args:
        value: Option value to fingerprint.

    Returns:
        The stable fingerprint, or None when the value cannot be represented.
    """
    from dj_hyperview.resolver import _source_fingerprint

    return _source_fingerprint(0, STUB, {"value": value})


def test_path_fingerprint_distinguishes_dialect_not_equivalent_posix_classes() -> None:
    """Path fingerprints tell Windows paths from POSIX ones, not concrete from pure."""
    raw = r"a\b"

    assert fingerprint(Path(raw)) == fingerprint(PurePosixPath(raw))
    assert fingerprint(PureWindowsPath(raw)) != fingerprint(PurePosixPath(raw))


def test_mapping_fingerprint_has_total_order_for_path_key_collisions() -> None:
    """Path keys that compare equal still give an order-independent fingerprint."""
    posix, windows = Path(r"a\b"), PureWindowsPath(r"a\b")
    first = {posix: "posix", windows: "windows"}
    reversed_order = {windows: "windows", posix: "posix"}

    assert first == reversed_order
    assert fingerprint(first) == fingerprint(reversed_order)


def test_mapping_fingerprint_is_canonical_for_heterogeneous_and_equal_keys() -> None:
    """Mixed and equal mapping keys give a canonical fingerprint."""
    values = [(1, "number"), ("1", "text"), (PurePosixPath("1"), "path")]
    first, reversed_order = dict(values), dict(reversed(values))

    assert fingerprint(first) == fingerprint(reversed_order)
    assert {True: "same"} == {1: "same"} == {1.0: "same"}
    assert fingerprint({True: "same"}) == fingerprint({1: "same"})
    assert fingerprint({1: "same"}) == fingerprint({1.0: "same"})


class PathReportingSource:
    """Cacheable source whose content is the class name of its path option.

    Args:
        value: Path whose class name becomes the template content.
    """

    cacheable = True

    def __init__(self, value: PurePath) -> None:
        """Keep the path option."""
        self.value = value

    def resolve(self, name: str) -> "ResolvedTemplate":
        """Resolve to a template whose content is the class name of the path.

        Args:
            name: Template name to resolve.

        Returns:
            A resolved template carrying the class name as content and revision.
        """
        from dj_hyperview.sources import ResolvedTemplate

        content = type(self.value).__name__
        return ResolvedTemplate(name, content, "path:x", "path", content)


@override_settings(CACHES=LOCMEM)
def test_path_dialect_change_does_not_return_stale_cached_content() -> None:
    """Changing the path dialect never returns stale cached content."""

    def source(value: PurePath) -> dict[str, object]:
        return {
            "BACKEND": f"{__name__}.PathReportingSource",
            "OPTIONS": {"value": value},
        }

    with override_settings(HYPERVIEW=configured(source(Path(r"a\b")))):
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content
            == "PosixPath"
        )
    with override_settings(HYPERVIEW=configured(source(PureWindowsPath(r"a\b")))):
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert resolved.content == "PureWindowsPath"


def test_fingerprint_is_canonical_for_supported_nested_configuration(
    tmp_path: Path,
) -> None:
    """Nested supported options give one ASCII fingerprint that hides their values.

    Args:
        tmp_path: Temporary directory used as a path option.
    """
    from dj_hyperview.resolver import _source_fingerprint

    left = {
        "secret": "contraseña-🦊",
        "nested": {
            "path": Path(tmp_path),
            "items": [1, True, None],
            "dialects": [PurePosixPath(r"á\b"), PureWindowsPath(r"á\b")],
        },
        "callable": fingerprint_callable,
        "dotted": "tests.stubs.TemplateSource",
    }
    right = {
        "dotted": left["dotted"],
        "callable": left["callable"],
        "nested": {
            "dialects": left["nested"]["dialects"],
            "items": [1, True, None],
            "path": Path(tmp_path),
        },
        "secret": left["secret"],
    }

    first = _source_fingerprint(0, STUB, left)
    second = _source_fingerprint(0, STUB, right)

    assert first == second
    assert first.isascii() and len(first) == 71
    assert "contraseña" not in first
    assert _source_fingerprint(1, STUB, left) != first


class WrongCallableIdentity:
    """Callable whose declared import identity belongs to another function."""

    __module__ = __name__
    __qualname__ = "fingerprint_callable"

    def __call__(self) -> None:
        """Return nothing."""
        return None


def test_fingerprint_handles_binary_float_and_unstable_callable_values() -> None:
    """Bytes and floats are supported, but infinity and unstable callables are not."""
    from dj_hyperview.resolver import _source_fingerprint

    assert _source_fingerprint(0, STUB, {"values": [b"raw", 1.5]})
    assert _source_fingerprint(0, STUB, {"value": float("inf")}) is None
    assert _source_fingerprint(0, STUB, {"value": lambda: None}) is None
    assert _source_fingerprint(0, STUB, {"value": WrongCallableIdentity()}) is None


class OpaqueSource:
    """Source without a fingerprintable configuration that counts its calls."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls = 0

    def resolve(self, name: str) -> "ResolvedTemplate":
        """Count the call and return a fixed raw template.

        Args:
            name: Template name to resolve.

        Returns:
            A resolved template with fixed content.
        """
        self.calls += 1
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(name, "raw", "opaque:x", "opaque", "r1")


@override_settings(CACHES=LOCMEM)
def test_unfingerprintable_manual_source_is_not_cached() -> None:
    """A manually built source without a fingerprint is never cached."""
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
def test_resolver_rejects_invalid_cache_wiring(kwargs: dict[str, object]) -> None:
    """The resolver rejects an unknown failure mode and mismatched source ids.

    Args:
        kwargs: Invalid cache wiring arguments for the resolver.
    """
    with pytest.raises(ValueError, match="Cache failure mode|cache identity"):
        TemplateResolver([OpaqueSource()], **kwargs)


class ConfiguredOpaqueSource:
    """Source configured with an option that cannot be fingerprinted.

    Args:
        content: Template text to serve.
        opaque: Option that cannot be fingerprinted, ignored.
    """

    calls = 0

    def __init__(self, content: str, opaque: object) -> None:
        """Keep the content and ignore the opaque option."""
        del opaque
        self.content = content

    def resolve(self, name: str) -> "ResolvedTemplate":
        """Count the call and serve the configured content.

        Args:
            name: Template name to resolve.

        Returns:
            A resolved template with the configured content.
        """
        type(self).calls += 1
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(name, self.content, "opaque:x", "opaque", "r1")


@override_settings(CACHES=LOCMEM)
def test_unfingerprintable_configured_options_disable_that_source_cache() -> None:
    """Options that cannot be fingerprinted disable the cache of that source."""
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
    """Cache backend that counts constructions and fails on any read or write.

    Args:
        location: Cache location, unused.
        params: Backend parameters passed to the base cache.
    """

    constructions = 0

    def __init__(self, location: str, params: dict[str, Any]) -> None:
        """Count the construction and initialize the base cache."""
        type(self).constructions += 1
        super().__init__(params)

    def get(
        self, key: str, default: object = None, version: int | None = None
    ) -> Never:
        """Fail, because a disabled cache must never be read.

        Args:
            key: Cache key, unused.
            default: Default value, unused.
            version: Key version, unused.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("disabled cache was read")

    def set(
        self,
        key: str,
        value: object,
        timeout: int | None = None,
        version: int | None = None,
    ) -> Never:
        """Fail, because a disabled cache must never be written.

        Args:
            key: Cache key, unused.
            value: Value, unused.
            timeout: Expiry, unused.
            version: Key version, unused.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("disabled cache was written")


@pytest.mark.parametrize("cache_setting", [pytest.param(None, id="absent"), {}])
def test_disabled_cache_never_initializes_cache_handler(
    cache_setting: dict[str, object] | None,
) -> None:
    """A disabled cache never initializes the cache handler.

    Args:
        cache_setting: CACHE setting that disables the cache, or None for none.
    """
    content = '<view xmlns="https://hyperview.org/hyperview" />'
    hyperview = {"SOURCES": [stub(content)]}
    if cache_setting is not None:
        hyperview["CACHE"] = cache_setting
    caches = {"default": {"BACKEND": f"{__name__}.CountingInitCache"}}
    CountingInitCache.constructions = 0

    with override_settings(CACHES=caches, HYPERVIEW=hyperview):
        get_settings()
        assert TemplateResolver.from_settings().resolve("screen.xml").content
        assert render_template("screen.xml") == content

    assert CountingInitCache.constructions == 0
