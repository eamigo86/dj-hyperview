"""Opt-in Redis compatibility profile tests."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import caches
from django.test import override_settings
from tests.compat.test_coverage_gate import write_report
from tools import test_matrix


def test_default_runner_removes_redis_configuration(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The canonical default gate never activates Redis implicitly."""
    environments: list[dict[str, str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(test_matrix.REDIS_URL_ENV, "redis://sensitive.invalid/0")
    monkeypatch.setenv(test_matrix.REDIS_OPT_IN_ENV, "1")

    def _fake_run(
        command: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> SimpleNamespace:
        environments.append(env)
        if len(environments) == 2:
            write_report(tmp_path / "coverage.xml")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_matrix.subprocess, "run", _fake_run)

    assert test_matrix.run_coverage(redis=False) == 0
    assert len(environments) == 2
    assert all(test_matrix.REDIS_URL_ENV not in env for env in environments)
    assert all(test_matrix.REDIS_OPT_IN_ENV not in env for env in environments)


def test_redis_runner_requires_explicit_service_url(
    monkeypatch: Any, capsys: Any
) -> None:
    """Redis opt-in fails safely before starting tests without a service URL."""
    monkeypatch.delenv(test_matrix.REDIS_URL_ENV, raising=False)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    assert test_matrix.run_coverage(redis=True) == 2
    assert calls == []
    assert "Redis profile requires an explicit service URL." in capsys.readouterr().err


def test_redis_runner_forwards_explicit_opt_in(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """An explicit Redis profile activates the live test in subprocesses."""
    environments: list[dict[str, str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(test_matrix.REDIS_URL_ENV, "redis://127.0.0.1:6379/15")

    def _fake_run(command, *, check, env):
        environments.append(env)
        if len(environments) == 2:
            write_report(tmp_path / "coverage.xml")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        _fake_run,
    )

    assert test_matrix.run_coverage(redis=True) == 0
    assert len(environments) == 2
    assert all(env[test_matrix.REDIS_OPT_IN_ENV] == "1" for env in environments)
    assert all(
        env[test_matrix.REDIS_URL_ENV] == "redis://127.0.0.1:6379/15"
        for env in environments
    )


@pytest.mark.skipif(
    os.environ.get("DJHV_TEST_REDIS") != "1",
    reason="Redis compatibility is explicitly opt-in",
)
def test_opt_in_redis_backend_round_trip() -> None:
    """The opt-in cell exercises Django's public Redis cache backend."""
    url = os.environ["DJHV_REDIS_URL"]
    configured = {
        "djhv-redis": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": url,
        }
    }

    with override_settings(CACHES=configured):
        cache = caches["djhv-redis"]
        cache.set("djhv:compatibility", "ready", timeout=30)
        assert cache.get("djhv:compatibility") == "ready"
        cache.delete("djhv:compatibility")
