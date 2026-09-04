"""Run the canonical compatibility and aggregate coverage gate."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence

SUPPORTED_PYTHONS = ("3.12", "3.13", "3.14")
SUPPORTED_DJANGOS = ("5.2.17", "6.1.1")
REDIS_OPT_IN_ENV = "DJHV_TEST_REDIS"
REDIS_URL_ENV = "DJHV_REDIS_URL"

_BASE_COVERAGE = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "--cov=dj_hyperview",
    "--cov-branch",
    "--cov-report=term-missing:skip-covered",
    "--cov-fail-under=0",
)
_ADMIN_COVERAGE = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "--ds=tests.settings_database_admin",
    "tests/test_database_admin.py",
    "tests/test_database_admin_publication.py",
    "--cov=dj_hyperview",
    "--cov-branch",
    "--cov-append",
    "--cov-report=term-missing:skip-covered",
    "--cov-fail-under=95",
)


def coverage_commands() -> tuple[tuple[str, ...], ...]:
    """Return the base and admin commands for honest aggregate coverage.

    Returns:
        Commands that measure the full base suite first and then append the
        optional admin profile before enforcing the coverage threshold.
    """
    return (_BASE_COVERAGE, _ADMIN_COVERAGE)


def matrix_commands() -> tuple[tuple[str, ...], ...]:
    """Return portable uv commands for every supported compatibility cell.

    Returns:
        Commands covering Python 3.12 through 3.14 and both supported Django
        patch releases.
    """
    return tuple(
        (
            "uv",
            "run",
            "--locked",
            "--python",
            python,
            "--with",
            f"Django=={django}",
            "python",
            "tools/test_matrix.py",
        )
        for python in SUPPORTED_PYTHONS
        for django in SUPPORTED_DJANGOS
    )


def _profile_environment(*, redis: bool) -> dict[str, str]:
    environment = os.environ.copy()
    if not redis:
        environment.pop(REDIS_OPT_IN_ENV, None)
        environment.pop(REDIS_URL_ENV, None)
        return environment
    if not environment.get(REDIS_URL_ENV):
        raise ValueError("Redis profile requires an explicit service URL.")
    environment[REDIS_OPT_IN_ENV] = "1"
    return environment


def run_coverage(*, redis: bool) -> int:
    """Execute canonical aggregate coverage with optional live Redis.

    Args:
        redis: Whether to enable the explicitly configured Redis acceptance.

    Returns:
        Zero when both coverage phases pass, otherwise the first failure code.
    """
    try:
        environment = _profile_environment(redis=redis)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    for command in coverage_commands():
        completed = subprocess.run(command, check=False, env=environment)
        if completed.returncode:
            return completed.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run coverage or print the supported compatibility matrix.

    Args:
        argv: Optional command-line arguments.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis",
        action="store_true",
        help="enable the live Redis cell using DJHV_REDIS_URL",
    )
    parser.add_argument(
        "--show-matrix",
        action="store_true",
        help="print reproducible uv commands instead of running tests",
    )
    arguments = parser.parse_args(argv)
    if arguments.show_matrix:
        for command in matrix_commands():
            print(shlex.join(command))
        return 0
    return run_coverage(redis=arguments.redis)


if __name__ == "__main__":
    raise SystemExit(main())
