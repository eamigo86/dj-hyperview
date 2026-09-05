"""Documentation-site configuration contract tests."""

import re
from importlib.metadata import version
from pathlib import Path

import yaml

from dj_hyperview import __all__ as public_api

ROOT = Path(__file__).parents[1]
DOCS = (
    "index.md",
    "installation.md",
    "quickstart.md",
    "configuration.md",
    "hyperview-0.110.0.md",
    "filesystem.md",
    "database-admin.md",
    "cache-consistency.md",
    "security.md",
    "api-reference.md",
    "testing.md",
    "contributing.md",
    "release-rollback.md",
)


def _documentation_pages() -> dict[str, str]:
    """Read every public documentation foundation page."""
    return {name: (ROOT / "docs" / name).read_text() for name in DOCS}


def _navigation_targets(items: list[dict[str, object]]) -> list[str]:
    """Return ordered leaf targets from nested Zensical navigation.

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


def test_zensical_configuration_uses_pinned_tool_and_portable_navigation() -> None:
    """The checked-in configuration names every portable documentation page."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())

    assert version("zensical") == "0.0.59"
    assert config["site_name"] == "dj-hyperview"
    assert config["site_url"] == "https://eamigo86.github.io/dj-hyperview/"
    assert config["repo_url"] == "https://github.com/eamigo86/dj-hyperview"
    assert config["docs_dir"] == "docs"
    assert config["site_dir"] == "site"
    assert config["site_author"] == "Ernesto Perez Amigo"
    assert config["edit_uri"] == "edit/main/docs/"
    assert config["theme"]["name"] == "material"
    assert config["theme"]["variant"] == "classic"
    assert {
        "navigation.tabs",
        "navigation.sections",
        "navigation.path",
        "content.code.copy",
    } <= set(config["theme"]["features"])
    assert config["nav"] == [
        {"Home": "index.md"},
        {
            "Getting Started": [
                {"Installation": "installation.md"},
                {"Quick Start": "quickstart.md"},
                {"Configuration": "configuration.md"},
                {"Hyperview compatibility": "hyperview-0.110.0.md"},
            ]
        },
        {
            "User Guide": [
                {"Filesystem sources": "filesystem.md"},
                {"Database and admin": "database-admin.md"},
                {"Cache consistency": "cache-consistency.md"},
                {"Security": "security.md"},
            ]
        },
        {"API Reference": [{"Public Python API": "api-reference.md"}]},
        {
            "Development": [
                {"Testing integrations": "testing.md"},
                {"Contributing": "contributing.md"},
                {"Release and rollback": "release-rollback.md"},
            ]
        },
    ]
    assert _navigation_targets(config["nav"]) == list(DOCS)
    assert len(_navigation_targets(config["nav"])) == len(
        set(_navigation_targets(config["nav"]))
    )


def test_repository_header_displays_stable_and_prerelease_versions() -> None:
    """The repository facts include the newest public GitHub release tag."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    source = (ROOT / "docs" / "overrides" / "partials" / "source.html").read_text()
    script = (ROOT / "docs" / "javascripts" / "source-facts.js").read_text()

    assert config["theme"]["custom_dir"] == "docs/overrides"
    assert config["extra_javascript"] == ["javascripts/source-facts.js"]
    assert "data-dj-hyperview-source" in source
    assert 'data-md-component="source"' not in source
    assert "/releases?per_page=1" in script
    assert "tag_name" in script
    assert "stargazers_count" in script
    assert "forks_count" in script
    assert "textContent" in script
    assert "innerHTML" not in script


def test_documentation_starts_with_installation_and_configuration_outcomes() -> None:
    """The landing page leads directly to both first-use outcomes."""
    pages = _documentation_pages()

    assert pages["index.md"].startswith("# dj-hyperview\n")
    assert "[Install dj-hyperview](installation.md)" in pages["index.md"]
    assert "[Configure template resolution](configuration.md)" in pages["index.md"]
    assert "uv add dj-hyperview" in pages["installation.md"]
    assert "Python 3.12" in pages["installation.md"]
    assert "Django 5.2" in pages["installation.md"]
    assert "HYPERVIEW" in pages["configuration.md"]
    assert "TemplateResolver" in pages["configuration.md"]


def test_readme_states_the_supported_schema_format() -> None:
    """The project overview prevents copying an incompatible upstream XSD."""
    readme = (ROOT / "README.md").read_text()

    assert "single-file XSD 1.0" in readme


def test_hyperview_manifest_link_targets_the_public_repository() -> None:
    """The compatibility manifest remains reachable from the hosted site."""
    page = _documentation_pages()["hyperview-0.110.0.md"]

    assert (
        "https://github.com/eamigo86/dj-hyperview/blob/main/"
        "tests/contracts/hyperview/0.110.0/manifest.json"
    ) in page
    assert "../tests/contracts/hyperview/0.110.0/manifest.json" not in page


def test_configuration_documents_every_builtin_source_option() -> None:
    """Consumers can discover constructor options for every bundled source."""
    page = _documentation_pages()["configuration.md"]

    assert "## Source backend options" in page
    assert "keyword arguments" in page
    assert "custom backend" in page
    assert "`FileSystemSource`" in page
    assert "`template_dirs`" in page
    assert '`HYPERVIEW["TEMPLATE_DIRS"]`' in page
    assert "ordered template roots" in page.lower()
    assert "`DatabaseSource`" in page
    assert "`using`" in page
    assert "database alias" in page
    assert "Django database routing" in page
    assert "source caching" in page
    assert "single string or Path" in page


def test_configuration_documents_deterministic_template_engine_selection() -> None:
    """Consumers can predict which Django template engine renders HXML."""
    page = _documentation_pages()["configuration.md"]

    assert "first configured DjangoTemplates backend" in page
    assert '`using="django"`' in page
    assert "does not switch engines" in page


def test_quickstart_and_reference_cover_the_supported_public_path() -> None:
    """The first-use path and public exports stay visible and complete."""
    pages = _documentation_pages()
    quickstart = pages["quickstart.md"]
    reference = pages["api-reference.md"]

    assert quickstart.startswith("# Quick Start\n")
    assert "screens/home.xml" in quickstart
    assert "HyperviewTemplateView" in quickstart
    assert "path(" in quickstart
    assert "application/vnd.hyperview+xml" in quickstart
    assert reference.startswith("# API Reference\n")
    for symbol in public_api:
        assert f"`{symbol}`" in reference


def test_api_reference_distinguishes_validation_and_cache_states() -> None:
    """Reference wording separates source, rendered, absent, and cached states."""
    reference = _documentation_pages()["api-reference.md"]

    assert "`validate_template_source`" in reference
    assert "raw template source" in reference
    assert "final rendered HXML document" in reference
    assert "explicit cached source miss" in reference
    assert "returns `None` when no cache entry exists" in reference


def test_contributing_guide_records_project_quality_contracts() -> None:
    """Contributors see the testing and public documentation conventions."""
    page = _documentation_pages()["contributing.md"]

    assert "Strict TDD" in page
    assert "95%" in page
    assert "Google-style" in page
    assert "type hints" in page
    assert "Args" in page and "Returns" in page and "Raises" in page
    assert "backticks" in page
    assert "uv run pytest" in page


def test_release_guide_documents_tag_driven_github_releases() -> None:
    """Release docs preserve tag identity through every hosted publication."""
    page = _documentation_pages()["release-rollback.md"]

    assert "GitHub Release" in page
    assert "after PyPI succeeds" in page
    assert "prerelease" in page
    assert "does not create a GitHub Release" not in page


def test_markdown_links_remain_inside_the_zensical_document_tree() -> None:
    """Local Markdown targets stay publishable by the strict site build."""
    docs_root = (ROOT / "docs").resolve()

    for source in docs_root.rglob("*.md"):
        for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]+)?\)", source.read_text()):
            if target.startswith(("https://", "http://")):
                continue
            resolved = (source.parent / target).resolve()
            assert resolved.is_relative_to(docs_root), (source, target)
            assert resolved.is_file(), (source, target)


def test_development_plan_stays_local_and_outside_public_navigation() -> None:
    """The private development record is ignored and never linked publicly."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    public_pages = "\n".join(
        (
            (ROOT / "README.md").read_text(),
            (ROOT / "docs" / "index.md").read_text(),
            (ROOT / "docs" / "contributing.md").read_text(),
        )
    )

    assert "development/README.md" not in _navigation_targets(config["nav"])
    assert "docs/development/" not in public_pages
    assert "development/README.md" not in public_pages
    assert "/docs/development/" in (ROOT / ".gitignore").read_text().splitlines()


def test_documentation_declares_foundation_limits_and_valid_local_links() -> None:
    """The foundation stays package-owned and does not promise future guides."""
    pages = _documentation_pages()

    assert "does not ship application screens" in pages["index.md"]
    assert "[Use filesystem sources](filesystem.md)" in pages["index.md"]
    for source in DOCS:
        for target in DOCS:
            if f"]({target})" in pages[source]:
                assert (ROOT / "docs" / target).is_file()


def test_repository_readme_presents_the_public_package_journey() -> None:
    """The repository front page mirrors the published package experience."""
    readme = (ROOT / "README.md").read_text()

    for badge in (
        "github/actions/workflow/status/eamigo86/dj-hyperview/ci.yml",
        "codecov.io/gh/eamigo86/dj-hyperview/graph/badge.svg",
        "pypi/pyversions/dj-hyperview",
        "pypi/frameworkversions/django/dj-hyperview",
        "pypi/v/dj-hyperview",
        "pepy/dt/dj-hyperview",
        "astral-sh/ruff/main/assets/badge/v2.json",
    ):
        assert badge in readme

    headings = (
        "## Requirements",
        "## Installation",
        "## Quick start",
        "## Configuration",
        "## Documentation",
        "## Development",
    )
    positions = tuple(readme.index(heading) for heading in headings)
    assert positions == tuple(sorted(positions))
    assert "uv add dj-hyperview" in readme
    assert "pip install dj-hyperview" in readme
    installation_pages = (
        readme,
        (ROOT / "docs" / "installation.md").read_text(),
        (ROOT / "docs" / "quickstart.md").read_text(),
    )
    assert all('"dj_hyperview"' in page for page in installation_pages)
    assert all(
        "dj_hyperview.apps.DjHyperviewConfig" not in page for page in installation_pages
    )
    assert "https://pypi.org/project/dj-hyperview/" in readme
    assert "https://eamigo86.github.io/dj-hyperview/" in readme
    assert "does not ship application screens" in readme
    assert "Every successfully claimed root or successor token" not in readme
