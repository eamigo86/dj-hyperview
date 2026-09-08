"""Backend capability regressions for the direct template-cache API."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.cache.backends.filebased import FileBasedCache
from django.core.cache.backends.locmem import LocMemCache
from django.test import override_settings

import dj_hyperview.cache as cache_module
from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable


class ConsumerFileCache(FileBasedCache):
    """A consumer subclass retains the parent's non-atomic add operation."""


@pytest.fixture
def memory_cache(monkeypatch: pytest.MonkeyPatch, request) -> Iterator[TemplateCache]:
    """Provide an isolated atomic backend for failure-contract checks."""
    backend = LocMemCache(request.node.nodeid, {})
    monkeypatch.setattr(cache_module, "caches", {"default": backend})
    with override_settings(CACHES={"default": {"BACKEND": "unused"}}):
        yield TemplateCache("failure-contract")


@pytest.mark.parametrize("backend_type", [FileBasedCache, ConsumerFileCache])
@override_settings(CACHES={"default": {"BACKEND": "unused"}})
def test_direct_cache_rejects_non_atomic_file_backends(
    backend_type: type[FileBasedCache], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct construction must not permit an unsafe generation backend."""
    backend = backend_type(str(tmp_path), {})
    monkeypatch.setattr(cache_module, "caches", {"default": backend})

    with pytest.raises(SourceUnavailable) as captured:
        TemplateCache("consumer")

    assert captured.value.reason == "unsupported backend"
    assert str(tmp_path) not in str(captured.value)
    assert list(tmp_path.iterdir()) == []


@override_settings(CACHES={"default": {"BACKEND": "unused"}})
def test_direct_cache_rechecks_backend_after_context_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long-lived wrapper must reject a later file-based backend too."""
    backends = {"default": LocMemCache("runtime-backend-change", {})}
    monkeypatch.setattr(cache_module, "caches", backends)
    cache = TemplateCache("consumer")
    backends["default"] = FileBasedCache(str(tmp_path), {})

    with pytest.raises(SourceUnavailable, match="unsupported backend"):
        _ = cache.backend


def test_generation_entropy_failure_remains_typed(memory_cache, monkeypatch) -> None:
    """Invalid entropy cannot create a malformed generation token."""
    monkeypatch.setattr(cache_module.secrets, "token_hex", lambda size: None)

    with pytest.raises(SourceUnavailable, match="backend failure"):
        memory_cache.generation("screen.xml")


@pytest.mark.parametrize("add_result", [None, False])
def test_unconfirmed_generation_initialization_fails_closed(
    memory_cache, monkeypatch, add_result
) -> None:
    """A non-boolean add or a lost competing generation is not success."""
    monkeypatch.setattr(memory_cache.backend, "add", lambda *a, **kw: add_result)

    with pytest.raises(SourceUnavailable, match="backend failure"):
        memory_cache.generation("screen.xml")


def test_silent_generation_write_failure_cannot_report_invalidation_success(
    memory_cache, monkeypatch
) -> None:
    """A backend acknowledgement must be checked against the stored token."""
    original = memory_cache.generation("screen.xml")
    monkeypatch.setattr(memory_cache.backend, "set", lambda *a, **kw: True)

    with pytest.raises(SourceUnavailable, match="backend failure"):
        memory_cache.invalidate("screen.xml")

    assert memory_cache.generation("screen.xml") == original


def test_resolved_read_failure_remains_typed(memory_cache, monkeypatch) -> None:
    """Resolved-cache reads must redact backend exception details too."""

    def fail_read(*args: object, **kwargs: object) -> None:
        raise RuntimeError("sensitive backend details")

    monkeypatch.setattr(memory_cache.backend, "get", fail_read)

    with pytest.raises(SourceUnavailable, match="backend failure") as captured:
        memory_cache.get_resolved("source", "screen.xml")

    assert "sensitive" not in str(captured.value)


@pytest.mark.parametrize(
    "payload",
    [b"untrusted bytes", '{"version":false}', '{"version":1,"state":"unknown"}'],
)
def test_resolved_envelope_shape_remains_fail_closed(memory_cache, payload) -> None:
    """Unsupported payloads must not become hits after backend hardening."""
    key = memory_cache.key("source", "screen.xml", "@resolved")
    memory_cache.backend.set(key, payload)

    with pytest.raises(SourceUnavailable, match="invalid payload"):
        memory_cache.get_resolved("source", "screen.xml")
