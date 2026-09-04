"""Operational documentation contract tests."""

import re
from inspect import signature
from pathlib import Path

import yaml

from dj_hyperview import TemplateResolver
from dj_hyperview.contrib.database.services import (
    delete_template,
    publish_template,
    rename_template,
)

ROOT = Path(__file__).parents[1]
SOURCE_GUIDES = ("filesystem.md", "database-admin.md")


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


def test_source_guides_are_navigable_and_match_the_landing_outcome() -> None:
    """The landing page promises resolution and links both source guides."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    targets = [target for item in config["nav"] for target in item.values()]
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
    assert len(blocks) >= 2
    for block in blocks:
        compile(block, "docs/filesystem.md", "exec")
    assert list(signature(TemplateResolver.from_settings).parameters) == []


def test_database_admin_guide_matches_public_services_and_optional_apps() -> None:
    """The database guide describes opt-in apps and exact service signatures."""
    page = _read_document("database-admin.md")
    blocks = _python_blocks(page)

    assert "dj_hyperview.contrib.database" in page
    assert "django.contrib.admin" in page
    assert "python manage.py migrate dj_hyperview_database" in page
    assert '"using": "default"' in page
    assert 'path("admin/", admin.site.urls)' in page
    assert "transaction.on_commit" in page
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
