import traceback
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import caches
from django.test import override_settings

from dj_hyperview.exceptions import SourceUnavailable, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-cache-proxy-optout",
    }
}


class _SharedSourceState:
    def __init__(self) -> None:
        self.content: str | None = "first"
        self.calls = 0
        self.marker_reads = 0
        self.marker_error: str | None = None
        self.failure: SourceUnavailable | None = None
        self._dj_hyperview_cacheable: object = False

    def resolve(self, name: str) -> ResolvedTemplate | None:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        if self.content is None:
            return None
        return ResolvedTemplate(name, self.content, "proxy", "proxy", "1")


_STATE = _SharedSourceState()
_SECOND_STATE = _SharedSourceState()


class _ForwardingSource:
    def __getattr__(self, name: str) -> Any:
        if name == "_dj_hyperview_cacheable":
            _STATE.marker_reads += 1
        return getattr(_STATE, name)


class _GetattributeSource:
    def __getattribute__(self, name: str) -> Any:
        if name == "_dj_hyperview_cacheable":
            _STATE.marker_reads += 1
            if _STATE.marker_error is not None:
                raise RuntimeError(_STATE.marker_error)
            return _STATE._dj_hyperview_cacheable
        return object.__getattribute__(self, name)

    def resolve(self, name: str) -> ResolvedTemplate | None:
        return _STATE.resolve(name)


class _AbsentDynamicSource:
    def __getattr__(self, name: str) -> Any:
        if name == "_dj_hyperview_cacheable":
            _STATE.marker_reads += 1
            raise AttributeError(name)
        return getattr(_STATE, name)


class _CachedSource:
    def resolve(self, name: str) -> ResolvedTemplate | None:
        return _SECOND_STATE.resolve(name)


@pytest.fixture(autouse=True)
def configured_cache() -> Iterator[None]:
    with override_settings(CACHES=LOCMEM_CACHES):
        caches["screens"].clear()
        for state in (_STATE, _SECOND_STATE):
            state.content = "first"
            state.calls = 0
            state.marker_reads = 0
            state.marker_error = None
            state.failure = None
            state._dj_hyperview_cacheable = False
        yield


def _config(backend: str, namespace: str) -> dict[str, object]:
    return {
        "SOURCES": [{"BACKEND": backend}],
        "CACHE": {"ALIAS": "screens", "NAMESPACE": namespace},
    }


def test_from_settings_honors_forwarded_false_for_content() -> None:
    config = _config(f"{__name__}._ForwardingSource", "proxy-forward-content")
    with override_settings(HYPERVIEW=config):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
        _STATE.content = "second"
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "second"
        )

    assert (_STATE.marker_reads, _STATE.calls) == (2, 2)


def test_from_settings_honors_forwarded_false_for_negative_miss() -> None:
    config = _config(f"{__name__}._ForwardingSource", "proxy-forward-miss")
    _STATE.content = None
    with override_settings(HYPERVIEW=config):
        with pytest.raises(TemplateNotFound):
            TemplateResolver.from_settings().resolve("screen.xml")
        _STATE.content = "second"
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "second"
        )

    assert (_STATE.marker_reads, _STATE.calls) == (2, 2)


def test_from_settings_honors_getattribute_false() -> None:
    config = _config(f"{__name__}._GetattributeSource", "proxy-getattribute")
    with override_settings(HYPERVIEW=config):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
        _STATE.content = "second"
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "second"
        )

    assert (_STATE.marker_reads, _STATE.calls) == (2, 2)


@pytest.mark.parametrize("marker", [None, 1, "false"], ids=["none", "one", "string"])
@pytest.mark.parametrize("initial", ["first", None], ids=["content", "miss"])
def test_dynamic_non_bool_marker_fails_closed(
    marker: object, initial: str | None
) -> None:
    _STATE._dj_hyperview_cacheable = marker
    _STATE.content = initial
    config = _config(
        f"{__name__}._GetattributeSource",
        f"proxy-non-bool-{initial is None}-{type(marker).__name__}",
    )
    with override_settings(HYPERVIEW=config):
        if initial is None:
            with pytest.raises(TemplateNotFound):
                TemplateResolver.from_settings().resolve("screen.xml")
        else:
            assert (
                TemplateResolver.from_settings().resolve("screen.xml").content
                == "first"
            )
        _STATE.content = "second"
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "second"
        )

    assert (_STATE.marker_reads, _STATE.calls) == (2, 2)


def test_dynamic_exact_true_marker_caches_and_is_read_once_per_identity() -> None:
    _STATE._dj_hyperview_cacheable = True
    config = _config(f"{__name__}._GetattributeSource", "proxy-exact-true")
    with override_settings(HYPERVIEW=config):
        current = TemplateResolver.from_settings()
        assert current.resolve("screen.xml").content == "first"
        _STATE.content = "second"
        assert current.resolve("screen.xml").content == "first"

    assert (_STATE.marker_reads, _STATE.calls) == (1, 1)


def test_dynamic_marker_error_fails_closed_without_hiding_content() -> None:
    _STATE.marker_error = "SENSITIVE-PROXY-MARKER"
    config = _config(f"{__name__}._GetattributeSource", "proxy-marker-error")
    with override_settings(HYPERVIEW=config):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
        _STATE.content = "second"
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "second"
        )

    assert (_STATE.marker_reads, _STATE.calls) == (2, 2)


def test_dynamic_marker_error_preserves_source_failure_without_leaking() -> None:
    failure = SourceUnavailable("origin", "actual source failure")
    _STATE.marker_error = "SENSITIVE-PROXY-MARKER"
    _STATE.failure = failure
    config = _config(f"{__name__}._GetattributeSource", "proxy-source-failure")

    with (
        override_settings(HYPERVIEW=config),
        pytest.raises(SourceUnavailable) as captured,
    ):
        TemplateResolver.from_settings().resolve("screen.xml")

    rendered = "".join(traceback.format_exception(captured.value))
    assert captured.value is failure
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "SENSITIVE-PROXY-MARKER" not in rendered
    assert (_STATE.marker_reads, _STATE.calls) == (1, 1)


def test_dynamic_attribute_error_is_the_legacy_absent_path() -> None:
    config = _config(f"{__name__}._AbsentDynamicSource", "proxy-absent")
    with override_settings(HYPERVIEW=config):
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"
        _STATE.content = "second"
        assert TemplateResolver.from_settings().resolve("screen.xml").content == "first"

    assert (_STATE.marker_reads, _STATE.calls) == (2, 1)


def test_dynamic_marker_failure_does_not_disable_another_source_cache() -> None:
    _STATE.marker_error = "SENSITIVE-PROXY-MARKER"
    _STATE.content = None
    _SECOND_STATE.content = "winner"
    config = {
        "SOURCES": [
            {"BACKEND": f"{__name__}._GetattributeSource"},
            {"BACKEND": f"{__name__}._CachedSource"},
        ],
        "CACHE": {"ALIAS": "screens", "NAMESPACE": "proxy-mixed"},
    }
    with override_settings(HYPERVIEW=config):
        current = TemplateResolver.from_settings()
        assert current.resolve("screen.xml").content == "winner"
        _SECOND_STATE.content = "changed"
        assert current.resolve("screen.xml").content == "winner"

    assert (_STATE.marker_reads, _STATE.calls, _SECOND_STATE.calls) == (1, 2, 1)
