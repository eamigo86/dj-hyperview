"""Reproducible Python and Django matrix policy tests."""

import runpy
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import test_matrix

ROOT = Path(__file__).parents[2]
# The oldest and newest supported patch releases: the runner re-executes itself for
# one that is not the active Django.
OLDEST, NEWEST = test_matrix.SUPPORTED_DJANGOS[0], test_matrix.SUPPORTED_DJANGOS[-1]


def test_supported_matrix_matches_metadata() -> None:
    """The runner and the project metadata name the same Pythons and Djangos."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    policy = metadata["tool"]["dj-hyperview"]

    assert tuple(policy["supported-python"]) == test_matrix.SUPPORTED_PYTHONS
    assert tuple(policy["supported-django"]) == test_matrix.SUPPORTED_DJANGOS


def test_coverage_commands_enforce_the_aggregate_after_admin() -> None:
    """The threshold applies only after real base and admin coverage combine."""
    base, admin = test_matrix.coverage_commands()

    assert "--cov-fail-under=0" in base
    assert not any(argument.startswith("tests/") for argument in base)
    assert "--cov-append" in admin
    assert "--cov-fail-under=95" in admin
    assert "--cov-report=xml:coverage.xml" in admin
    assert "coverage.xml" in (ROOT / ".gitignore").read_text().splitlines()
    assert {
        "tests/test_database_admin.py",
        "tests/test_database_admin_validation.py",
        "tests/test_database_admin_schema_r2.py",
        "tests/test_database_admin_publication.py",
    }.issubset(admin)


def test_requested_django_version_reexecutes_the_canonical_runner(
    monkeypatch: Any,
) -> None:
    """A requested patch release runs the same gate in a locked uv overlay."""
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: NEWEST)

    def _fake_run(
        command: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> SimpleNamespace:
        seen.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_matrix.subprocess, "run", _fake_run)

    assert test_matrix.main(["--django-version", OLDEST]) == 0
    assert seen == [
        (
            "uv",
            "run",
            "--locked",
            "--with",
            f"Django=={OLDEST}",
            "python",
            "-m",
            "tools.test_matrix",
        )
    ]


def test_requested_installed_django_runs_coverage_without_reexec(
    monkeypatch: Any,
) -> None:
    """The active supported patch release runs coverage directly."""
    seen: list[bool] = []
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: OLDEST)
    monkeypatch.setattr(
        test_matrix,
        "run_coverage",
        lambda *, redis: seen.append(redis) or 0,
    )

    assert test_matrix.main(["--django-version", OLDEST]) == 0
    assert seen == [False]


def test_django_selector_redis_requires_url_before_overlay(
    monkeypatch: Any, capsys: Any
) -> None:
    """A Redis selector fails closed before starting an overlay without a URL."""
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: NEWEST)
    monkeypatch.delenv(test_matrix.REDIS_URL_ENV, raising=False)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    assert test_matrix.main(["--django-version", OLDEST, "--redis"]) == 2
    assert calls == []
    assert capsys.readouterr().err == (
        "Redis profile requires an explicit service URL.\n"
    )


def test_django_selector_forwards_redis_and_overlay_failure(
    monkeypatch: Any,
) -> None:
    """An explicit Redis selector forwards opt-in and subprocess status."""
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: NEWEST)
    monkeypatch.setenv(test_matrix.REDIS_URL_ENV, "redis://127.0.0.1:6379/15")

    def _fake_run(
        command: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> SimpleNamespace:
        seen.append((command, env))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(test_matrix.subprocess, "run", _fake_run)

    assert test_matrix.main(["--django-version", OLDEST, "--redis"]) == 7
    assert seen[0][0][-1] == "--redis"
    assert seen[0][1][test_matrix.REDIS_OPT_IN_ENV] == "1"


def test_main_without_selector_uses_active_environment(monkeypatch: Any) -> None:
    """The default CLI delegates directly to aggregate coverage."""
    seen: list[bool] = []
    monkeypatch.setattr(
        test_matrix,
        "run_coverage",
        lambda *, redis: seen.append(redis) or 9,
    )

    assert test_matrix.main([]) == 9
    assert seen == [False]


def test_module_entrypoint_exits_with_the_status_of_main(
    monkeypatch: Any, capsys: Any
) -> None:
    """The executable module propagates the main return code."""
    monkeypatch.delenv(test_matrix.REDIS_URL_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["tools.test_matrix", "--redis"])

    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / "tools" / "test_matrix.py"), run_name="__main__")

    assert error.value.code == 2
    assert (
        capsys.readouterr().err == "Redis profile requires an explicit service URL.\n"
    )


@pytest.mark.parametrize("selector", ["6.1", "../../outside"])
def test_django_selector_rejects_unapproved_versions_and_paths(
    monkeypatch: Any, selector: str
) -> None:
    """Only exact audited Django patch releases are selectable."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    with pytest.raises(SystemExit) as error:
        test_matrix.main(["--django-version", selector])

    assert error.value.code == 2
    assert calls == []
