"""Isolation tests for package-owned consumer capabilities."""

from __future__ import annotations

import json

import pytest
from tools.package_guard import validate_project

from tests.consumer_project.process import PROJECT_ROOT, run_consumer


@pytest.mark.parametrize(
    ("settings_module", "expected"),
    [
        ("settings_base", [False, False, 4]),
        ("settings_filesystem", [False, False, 4]),
        ("settings_cache", [False, False, 4]),
        ("settings_database", [True, False, 4]),
        ("settings_admin", [True, True, 1]),
        ("settings_admin_postcommit", [True, True, 2]),
    ],
)
def test_clean_startup_loads_only_enabled_optional_apps(
    settings_module: str, expected: list[bool | int]
) -> None:
    """Each clean process imports only capabilities selected by its settings."""
    result = run_consumer(
        f"tests.consumer_project.{settings_module}",
        r"""
import json
import socket
import sys


def forbidden_optional_io(*args, **kwargs):
    raise AssertionError("startup performed optional I/O")


from django.core.cache import CacheHandler
from django.db.backends.base.base import BaseDatabaseWrapper

CacheHandler.__getitem__ = forbidden_optional_io
BaseDatabaseWrapper.cursor = forbidden_optional_io
socket.create_connection = forbidden_optional_io

import django

django.setup()

from django.urls import get_resolver
print(json.dumps([
    "dj_hyperview.contrib.database.models" in sys.modules,
    "django.contrib.admin" in sys.modules,
    len(get_resolver().url_patterns),
]))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize(
    "settings_module",
    [
        "settings_base",
        "settings_filesystem",
        "settings_cache",
        "settings_database",
        "settings_admin",
        "settings_admin_postcommit",
    ],
)
def test_consumer_profiles_pass_framework_checks(settings_module: str) -> None:
    """Every optional capability profile satisfies Django system checks."""
    result = run_consumer(
        f"tests.consumer_project.{settings_module}",
        "import django; django.setup(); from django.core.management import "
        'call_command; call_command("check", verbosity=0)',
    )

    assert result.returncode == 0, result.stderr


def test_consumer_capabilities_remain_outside_runtime_package() -> None:
    """Consumer settings and fixtures never become runtime package content."""
    consumer = PROJECT_ROOT / "tests" / "consumer_project"
    fixtures = PROJECT_ROOT / "tests" / "fixtures" / "consumer_project"
    runtime = PROJECT_ROOT / "src" / "dj_hyperview"
    consumer_files = [*consumer.rglob("*.py"), *fixtures.rglob("*.xml")]

    assert validate_project(PROJECT_ROOT) == []
    assert consumer_files
    assert all(path.is_relative_to(PROJECT_ROOT / "tests") for path in consumer_files)
    assert not list(runtime.rglob("*.xml"))
    assert not list(runtime.rglob("*.hxml"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in consumer_files)
    for forbidden in ("/Users/", "django_hv", "mobile::", "contact", "icon", "user"):
        assert forbidden not in combined
