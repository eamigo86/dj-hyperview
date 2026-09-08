"""Run the canonical compatibility and aggregate coverage gate."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from xml.etree import ElementTree

SUPPORTED_PYTHONS = ("3.12", "3.13", "3.14")
SUPPORTED_DJANGOS = ("5.2.17", "6.1.1")
REDIS_OPT_IN_ENV = "DJHV_TEST_REDIS"
REDIS_URL_ENV = "DJHV_REDIS_URL"
COVERAGE_REPORT = Path("coverage.xml")
COVERAGE_MINIMUM = 95

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
    "tests/test_database_admin_preview.py",
    "--cov=dj_hyperview",
    "--cov-branch",
    "--cov-append",
    "--cov-report=term-missing:skip-covered",
    "--cov-report=xml:coverage.xml",
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


def check_coverage(report: Path) -> int:
    """Enforce independent exact line and branch coverage thresholds.

    Args:
        report: Coverage.py XML report produced after all test profiles.

    Returns:
        Zero when both dimensions reach 95 percent, one when either falls
        short, or two when the report is missing, invalid, or empty.
    """
    try:
        root = ElementTree.parse(report).getroot()
        if root.tag != "coverage":
            raise ValueError("unexpected report root")
        dimensions = []
        for label in ("lines", "branches"):
            values = (root.get(f"{label}-covered"), root.get(f"{label}-valid"))
            if any(
                value is None or not value.isascii() or not value.isdecimal()
                for value in values
            ):
                raise ValueError("invalid coverage counter")
            covered, total = (int(value) for value in values)
            if total <= 0 or covered > total:
                raise ValueError("invalid coverage total")
            dimensions.append((label, covered, total))
    except (OSError, ValueError, ElementTree.ParseError):
        print(
            "Invalid coverage report: positive integer totals are required.",
            file=sys.stderr,
        )
        return 2

    result = 0
    for label, covered, total in dimensions:
        passed = covered * 100 >= total * COVERAGE_MINIMUM
        print(
            f"Coverage {label}: {covered}/{total}; minimum {COVERAGE_MINIMUM}%.",
            file=sys.stdout if passed else sys.stderr,
        )
        if not passed:
            result = 1
    return result


def run_coverage(*, redis: bool) -> int:
    """Execute canonical aggregate coverage with optional live Redis.

    Args:
        redis: Whether to enable the explicitly configured Redis acceptance.

    Returns:
        Zero when both profiles and independent coverage thresholds pass,
        otherwise the first failure code. Invalid reports return two.
    """
    try:
        environment = _profile_environment(redis=redis)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    try:
        COVERAGE_REPORT.unlink(missing_ok=True)
    except OSError:
        print("Cannot reset the coverage report before testing.", file=sys.stderr)
        return 2
    for command in coverage_commands():
        completed = subprocess.run(command, check=False, env=environment)
        if completed.returncode:
            return completed.returncode
    return check_coverage(COVERAGE_REPORT)


def _run_for_django(version: str, *, redis: bool) -> int:
    if metadata.version("Django") == version:
        return run_coverage(redis=redis)
    try:
        environment = _profile_environment(redis=redis)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    command = (
        "uv",
        "run",
        "--locked",
        "--with",
        f"Django=={version}",
        "python",
        "-m",
        "tools.test_matrix",
    )
    if redis:
        command += ("--redis",)
    return subprocess.run(command, check=False, env=environment).returncode


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
        "--django-version",
        choices=SUPPORTED_DJANGOS,
        help="run the gate under one supported Django patch release",
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
    if arguments.django_version:
        return _run_for_django(arguments.django_version, redis=arguments.redis)
    return run_coverage(redis=arguments.redis)


if __name__ == "__main__":
    raise SystemExit(main())
