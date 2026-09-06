"""Acceptance tests for the package-owned synthetic Django consumer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_consumer_probe() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    python_path = [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)]
    if inherited := env.get("PYTHONPATH"):
        python_path.append(inherited)
    env.update(
        DJANGO_SETTINGS_MODULE="tests.consumer_project.settings_base",
        PYTHONPATH=os.pathsep.join(python_path),
    )
    script = r'''
import importlib.abc
import json
import socket
import sys


class LegacyImportBlocker(importlib.abc.MetaPathFinder):
    """Reject imports from the abandoned package namespace."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "django_hv" or fullname.startswith("django_hv."):
            raise AssertionError("legacy package import attempted")
        return None


def forbidden_optional_access(*args, **kwargs):
    raise AssertionError("optional infrastructure access attempted")


sys.meta_path.insert(0, LegacyImportBlocker())
from django.core.cache import CacheHandler
from django.db.backends.base.base import BaseDatabaseWrapper

original_cache_getitem = CacheHandler.__getitem__
CacheHandler.__getitem__ = forbidden_optional_access
BaseDatabaseWrapper.cursor = forbidden_optional_access
socket.create_connection = forbidden_optional_access

import django

django.setup()

from django.apps import apps
from django.core.checks import run_checks
from django.urls import get_resolver
from dj_hyperview import TemplateResolver

resolver = TemplateResolver.from_settings()
package_checks = run_checks(tags=["dj_hyperview"])
CacheHandler.__getitem__ = original_cache_getitem
all_checks = run_checks()
print(json.dumps({
    "app": apps.get_app_config("dj_hyperview").name,
    "cache_disabled": resolver.cache is None,
    "checks": sorted({message.id for message in [*package_checks, *all_checks]}),
    "legacy_loaded": any(
        name == "django_hv" or name.startswith("django_hv.") for name in sys.modules
    ),
    "patterns": len(get_resolver().url_patterns),
    "sources": len(resolver.sources),
}))
'''
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_consumer_settings_use_minimal_public_package_contract() -> None:
    """The consumer installs the public app with no optional infrastructure."""
    consumer_settings = import_module("tests.consumer_project.settings_base")

    assert consumer_settings.INSTALLED_APPS == ["dj_hyperview"]
    assert consumer_settings.HYPERVIEW == {}
    assert consumer_settings.ROOT_URLCONF == "tests.consumer_project.urls"
    assert "DATABASES" not in vars(consumer_settings)
    assert "CACHES" not in vars(consumer_settings)


def test_consumer_starts_safely_through_public_package_apis() -> None:
    """A minimal process starts and reports its intentionally empty resolver."""
    completed = _run_consumer_probe()

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "app": "dj_hyperview",
        "cache_disabled": True,
        "checks": ["dj_hyperview.W005"],
        "legacy_loaded": False,
        "patterns": 4,
        "sources": 0,
    }


def test_consumer_scaffold_contains_only_portable_generic_configuration() -> None:
    """Consumer configuration remains repository-relative and domain neutral."""
    consumer_dir = PROJECT_ROOT / "tests" / "consumer_project"
    source_paths = sorted(consumer_dir.glob("*.py"))
    python_sources = [path.read_text(encoding="utf-8") for path in source_paths]

    assert {path.name for path in source_paths} >= {
        "__init__.py",
        "settings_base.py",
        "urls.py",
    }
    combined = "\n".join(python_sources)
    for forbidden in (
        "/Users/",
        "django_hv",
        "mobile::",
        "contact",
        "icon",
        "user",
    ):
        assert forbidden not in combined
