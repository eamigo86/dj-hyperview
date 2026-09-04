"""Reproducible Python and Django matrix policy tests."""

import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools import test_matrix

ROOT = Path(__file__).parents[2]


def test_supported_matrix_matches_metadata_and_builds_portable_commands() -> None:
    """Every supported Python and Django pair has one portable uv command."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    policy = metadata["tool"]["dj-hyperview"]
    commands = test_matrix.matrix_commands()

    assert metadata["project"]["requires-python"] == ">=3.12,<3.15"
    assert tuple(policy["supported-python"]) == test_matrix.SUPPORTED_PYTHONS
    assert tuple(policy["supported-django"]) == test_matrix.SUPPORTED_DJANGOS
    assert test_matrix.SUPPORTED_PYTHONS == ("3.12", "3.13", "3.14")
    assert test_matrix.SUPPORTED_DJANGOS == ("5.2.17", "6.1.1")
    assert len(commands) == 6
    assert {
        (command[command.index("--python") + 1], command[-3]) for command in commands
    } == {
        (python, f"Django=={django}")
        for python in test_matrix.SUPPORTED_PYTHONS
        for django in test_matrix.SUPPORTED_DJANGOS
    }
    assert all(
        command[-2:] == ("python", "tools/test_matrix.py") for command in commands
    )
    assert all(str(ROOT) not in " ".join(command) for command in commands)


def test_coverage_commands_enforce_the_aggregate_after_admin() -> None:
    """The threshold applies only after real base and admin coverage combine."""
    base, admin = test_matrix.coverage_commands()

    assert "--cov-fail-under=0" in base
    assert not any(argument.startswith("tests/") for argument in base)
    assert "--cov-append" in admin
    assert "--cov-fail-under=95" in admin
    assert {
        "tests/test_database_admin.py",
        "tests/test_database_admin_publication.py",
    }.issubset(admin)


@pytest.mark.parametrize(("codes", "expected_calls"), [([7], 1), ([0, 9], 2)])
def test_coverage_runner_stops_after_a_failed_phase(
    monkeypatch: Any, codes: list[int], expected_calls: int
) -> None:
    """Any failed phase prevents a misleading aggregate coverage result."""
    seen: list[tuple[str, ...]] = []
    remaining = iter(codes)

    def _fake_run(
        command: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> SimpleNamespace:
        seen.append(command)
        return SimpleNamespace(returncode=next(remaining))

    monkeypatch.setattr(test_matrix.subprocess, "run", _fake_run)

    assert test_matrix.run_coverage(redis=False) == codes[-1]
    assert seen == list(test_matrix.coverage_commands()[:expected_calls])


def test_requested_django_version_reexecutes_the_canonical_runner(
    monkeypatch: Any,
) -> None:
    """A requested patch release runs the same gate in a locked uv overlay."""
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: "6.1.1")

    def _fake_run(
        command: tuple[str, ...], *, check: bool, env: dict[str, str]
    ) -> SimpleNamespace:
        seen.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_matrix.subprocess, "run", _fake_run)

    assert test_matrix.main(["--django-version", "5.2.17"]) == 0
    assert seen == [
        (
            "uv",
            "run",
            "--locked",
            "--with",
            "Django==5.2.17",
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
    monkeypatch.setattr(test_matrix.metadata, "version", lambda name: "5.2.17")
    monkeypatch.setattr(
        test_matrix,
        "run_coverage",
        lambda *, redis: seen.append(redis) or 0,
    )

    assert test_matrix.main(["--django-version", "5.2.17"]) == 0
    assert seen == [False]
