"""Integration tests for package-owned optional consumer stacks."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from tests.consumer_project.process import run_consumer


def test_capability_settings_are_explicit_and_separate() -> None:
    """Database, cache, and admin profiles enable only their own capabilities."""
    database = import_module("tests.consumer_project.settings_database")
    cache = import_module("tests.consumer_project.settings_cache")
    admin = import_module("tests.consumer_project.settings_admin")
    admin_postcommit = import_module("tests.consumer_project.settings_admin_postcommit")

    assert "dj_hyperview.contrib.database" in database.INSTALLED_APPS
    assert "django.contrib.admin" not in database.INSTALLED_APPS
    assert "CACHES" not in vars(database)
    assert "dj_hyperview.contrib.database" not in cache.INSTALLED_APPS
    assert cache.HYPERVIEW["CACHE"]["ALIAS"] == "default"
    assert "django.contrib.admin" in admin.INSTALLED_APPS
    assert admin.ROOT_URLCONF == "tests.consumer_project.urls_admin"
    assert admin_postcommit.HYPERVIEW["CACHE"]["ALIAS"] == "default"
    assert "django.contrib.admin" in admin_postcommit.INSTALLED_APPS


def test_database_source_precedes_filesystem_and_migration_reverses(
    tmp_path: Path,
) -> None:
    """The optional model resolves active rows and falls through inactive rows."""
    result = run_consumer(
        "tests.consumer_project.settings_database",
        r"""
import json
import django

django.setup()

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from dj_hyperview import TemplateResolver

call_command("migrate", "dj_hyperview_database", verbosity=0)
model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
table = model._meta.db_table
model.objects.create(
    name="precedence.xml", content="<view>database</view>", revision=7
)
model.objects.create(
    name="fragments/item.xml", content="<view>inactive</view>", active=False
)
resolver = TemplateResolver.from_settings()
database = resolver.resolve("precedence.xml")
fallback = resolver.resolve("fragments/item.xml")
applied = table in connection.introspection.table_names()
call_command("migrate", "dj_hyperview_database", "zero", verbosity=0)
reversed_cleanly = table not in connection.introspection.table_names()
print(json.dumps({
    "applied": applied,
    "database": [database.content, database.origin, database.revision],
    "fallback": [fallback.source, fallback.name],
    "reversed": reversed_cleanly,
}))
""",
        database=tmp_path / "consumer.sqlite3",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "applied": True,
        "database": ["<view>database</view>", "database:precedence.xml", "7"],
        "fallback": ["filesystem", "fragments/item.xml"],
        "reversed": True,
    }


def test_locmem_cache_preserves_hit_miss_empty_and_public_invalidation(
    tmp_path: Path,
) -> None:
    """Opt-in LocMem caching distinguishes all raw source outcomes."""
    (tmp_path / "screen.xml").write_text("<view>old</view>", encoding="utf-8")
    (tmp_path / "empty.xml").write_text("", encoding="utf-8")
    result = run_consumer(
        "tests.consumer_project.settings_cache",
        r"""
import json
from pathlib import Path
import django

django.setup()

from django.conf import settings
from dj_hyperview import TemplateNotFound, TemplateResolver, invalidate_templates

root = Path(settings.HYPERVIEW["TEMPLATE_DIRS"][0])
resolver = TemplateResolver.from_settings()
old = resolver.resolve("screen.xml").content
(root / "screen.xml").write_text("<view>new</view>", encoding="utf-8")
cached = resolver.resolve("screen.xml").content
invalidate_templates("screen.xml")
refreshed = resolver.resolve("screen.xml").content
empty = resolver.resolve("empty.xml").content
(root / "empty.xml").write_text("<view>filled</view>", encoding="utf-8")
empty_cached = resolver.resolve("empty.xml").content
try:
    resolver.resolve("missing.xml")
except TemplateNotFound:
    first_miss = True
(root / "missing.xml").write_text("<view>created</view>", encoding="utf-8")
try:
    resolver.resolve("missing.xml")
except TemplateNotFound:
    cached_miss = True
invalidate_templates("empty.xml", "missing.xml")
print(json.dumps({
    "content": [old, cached, refreshed],
    "empty": [empty, empty_cached, resolver.resolve("empty.xml").content],
    "miss": [first_miss, cached_miss, resolver.resolve("missing.xml").content],
}))
""",
        template_dir=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "content": ["<view>old</view>", "<view>old</view>", "<view>new</view>"],
        "empty": ["", "", "<view>filled</view>"],
        "miss": [True, True, "<view>created</view>"],
    }
