"""Operational documentation contract tests."""

import re
from inspect import Parameter, signature
from pathlib import Path

import yaml

from dj_hyperview import TemplateResolver, invalidate_templates, validate_hxml
from dj_hyperview.contrib.database.services import (
    delete_template,
    publish_template,
    rename_template,
)

ROOT = Path(__file__).parents[1]
SOURCE_GUIDES = ("filesystem.md", "database-admin.md")
OPERATIONAL_GUIDES = (
    "cache-consistency.md",
    "security.md",
    "testing.md",
    "release-rollback.md",
    "http-responses.md",
)


def _read_document(name: str) -> str:
    """Read one package documentation page.

    Args:
        name: Documentation filename relative to the docs directory.

    Returns:
        The page text.
    """
    return (ROOT / "docs" / name).read_text()


def _python_blocks(text: str) -> list[str]:
    """Extract Python code fences from a documentation page.

    Args:
        text: Markdown page text.

    Returns:
        Python source blocks in document order.
    """
    return re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)


def _navigation_targets(items: list[dict[str, object]]) -> list[str]:
    """Return leaf documentation paths from nested navigation.

    Args:
        items: Nested navigation entries.

    Returns:
        Documentation paths in navigation order.
    """
    targets: list[str] = []
    for item in items:
        for value in item.values():
            if isinstance(value, str):
                targets.append(value)
            elif isinstance(value, list):
                targets.extend(_navigation_targets(value))
    return targets


def test_source_guides_are_navigable_and_match_the_landing_outcome() -> None:
    """The landing page promises resolution and links both source guides."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    targets = _navigation_targets(config["nav"])
    index = _read_document("index.md")

    assert all(guide in targets for guide in SOURCE_GUIDES)
    assert "Resolve a canonical template name" in index
    assert "Render a canonical template name" not in index
    assert "[Use filesystem sources](filesystem.md)" in index
    assert "[Publish through the database](database-admin.md)" in index


def test_filesystem_guide_uses_the_public_resolver_contract() -> None:
    """The filesystem happy path is portable, compilable, and cache-free."""
    page = _read_document("filesystem.md")
    blocks = _python_blocks(page)

    assert "dj_hyperview.sources.FileSystemSource" in page
    assert "TemplateResolver.from_settings().resolve" in page
    assert '"TEMPLATE_DIRS"' in page
    assert '"CACHE"' not in page
    assert "include/extends" in page
    assert "root symlink changes" in page
    assert "invalid_encoding" in page
    assert "SourceUnavailable" in page
    assert "Includes and extensions" not in page
    assert len(blocks) >= 2
    for block in blocks:
        compile(block, "docs/filesystem.md", "exec")
    assert list(signature(TemplateResolver.from_settings).parameters) == []


def test_database_admin_guide_matches_public_services_and_optional_apps() -> None:
    """The database guide describes opt-in apps and exact service signatures."""
    page = _read_document("database-admin.md")
    normalized = " ".join(page.split())
    blocks = _python_blocks(page)

    assert "dj_hyperview.contrib.database" in page
    assert "django.contrib.admin" in page
    assert "python manage.py migrate dj_hyperview_database" in page
    assert '"using": "default"' in page
    assert 'path("admin/", admin.site.urls)' in page
    assert "transaction.on_commit" in page
    assert "multi-root and text-only partials" in page
    assert "render-time validation" in page
    assert "render or publish_and_render" in normalized
    assert "no longer canonical" in normalized
    assert "remains recoverable" in normalized
    assert "one commit-aware invalidation" in normalized
    assert "byte-exact" in page
    assert "case-insensitive" in page
    assert len(blocks) >= 2
    for block in blocks:
        compile(block, "docs/database-admin.md", "exec")
    assert list(signature(publish_template).parameters) == [
        "name",
        "content",
        "active",
        "expected_revision",
        "using",
    ]
    assert list(signature(rename_template).parameters)[:2] == [
        "current_name",
        "new_name",
    ]
    assert list(signature(delete_template).parameters) == [
        "name",
        "expected_revision",
        "using",
    ]


def test_operational_guides_are_navigable_and_locally_linked() -> None:
    """Every operational outcome is reachable through portable local links."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    targets = _navigation_targets(config["nav"])
    index = _read_document("index.md")

    assert all(guide in targets for guide in OPERATIONAL_GUIDES)
    for guide in OPERATIONAL_GUIDES:
        assert f"]({guide})" in index
    for source in ("index.md", *SOURCE_GUIDES, *OPERATIONAL_GUIDES):
        page = _read_document(source)
        for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]+)?\)", page):
            assert (ROOT / "docs" / target).is_file(), (source, target)
        for block in _python_blocks(page):
            compile(block, f"docs/{source}", "exec")


def test_cache_guide_matches_opt_in_and_invalidation_contracts() -> None:
    """Cache guidance uses the public API and states coherence boundaries."""
    page = _read_document("cache-consistency.md")
    normalized = " ".join(page.split())
    blocks = _python_blocks(page)
    parameter = signature(invalidate_templates).parameters["names"]

    assert "django.core.cache.backends.locmem.LocMemCache" in page
    for key in ("ALIAS", "NAMESPACE", "TTL", "NEGATIVE_TTL", "FAILURE_MODE"):
        assert f'"{key}"' in page
    assert "bypass" in page and "raise" in page
    assert "unknown template misses create no cache metadata" in page.lower()
    assert "one non-expiring generation key" in page.lower()
    assert "DummyCache" in page
    assert "Omit the `CACHE` section" in normalized
    assert "older transaction snapshot" in normalized
    assert "ATOMIC_REQUESTS" in page
    assert "compiled templates" in page
    assert "filesystem deploy" in page.lower()
    assert "rotate" in page.lower() and "NAMESPACE" in page
    assert parameter.kind is Parameter.VAR_POSITIONAL
    assert blocks
    for block in blocks:
        compile(block, "docs/cache-consistency.md", "exec")


def test_security_guide_matches_name_and_xml_validation_contracts() -> None:
    """Security guidance documents canonical names and fail-closed XML limits."""
    page = _read_document("security.md")
    database_page = _read_document("database-admin.md")
    blocks = _python_blocks(page)

    for key in ("MODE", "SCHEMA", "MAX_BYTES", "MAX_DEPTH", "MAX_NODES"):
        assert f'"{key}"' in page
    assert "DTD" in page and "entities" in page
    assert "control characters" in page and "isolated surrogates" in page
    assert "includes, imports, and redefines" in page
    assert "executable Django template code" in page
    assert "developer-level trust" in page
    assert "developer-level trust" in database_page
    assert "hv_csrf_token" in page
    assert "load dj_hyperview" in page
    assert "XML declaration" in page
    assert "single-file XSD 1.0" in page
    assert list(signature(validate_hxml).parameters) == ["document", "config"]
    assert blocks
    for block in blocks:
        compile(block, "docs/security.md", "exec")


def test_testing_and_release_guides_use_reproducible_commands() -> None:
    """Testing and release instructions keep compatibility and rollback explicit."""
    testing = _read_document("testing.md")
    release = _read_document("release-rollback.md")

    assert "--django-version 5.2.17" in testing
    assert "--django-version 6.1.1" in testing
    assert "tests/consumer_project" in testing
    build_lines = [line for line in release.splitlines() if "zensical build" in line]
    assert build_lines
    assert all("-f zensical.yml" in line for line in build_lines)
    assert all("--strict" in line for line in build_lines)
    assert "CI" in release and "rollback" in release.lower()


def test_release_guide_documents_trusted_environments_and_recovery() -> None:
    """Release operators get an ordered, token-free publish and rollback path."""
    release = _read_document("release-rollback.md")

    assert "PyPI Trusted Publisher" in release
    assert "`pypi`" in release and "`github-pages`" in release
    assert "API token" in release and "not" in release
    assert "v0.1.0" in release
    assert "yank" in release.lower()
    assert "PyPI succeeds" in release
    assert "GitHub Release" in release
    assert "Only the PyPI job receives" not in release
    assert "PyPI and Pages jobs receive OIDC only within their own jobs" in release
    assert "Only Pages receives `pages: write`" in release
