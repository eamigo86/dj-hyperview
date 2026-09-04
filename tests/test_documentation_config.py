"""Documentation-site configuration contract tests."""

from importlib.metadata import version
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
DOCS = (
    "index.md",
    "installation.md",
    "configuration.md",
    "filesystem.md",
    "database-admin.md",
)


def _documentation_pages() -> dict[str, str]:
    """Read every public documentation foundation page."""
    return {name: (ROOT / "docs" / name).read_text() for name in DOCS}


def test_zensical_configuration_uses_pinned_tool_and_portable_navigation() -> None:
    """The checked-in configuration names every portable documentation page."""
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())

    assert version("zensical") == "0.0.59"
    assert config["site_name"] == "dj-hyperview"
    assert config["site_url"] == "https://eamigo86.github.io/dj-hyperview/"
    assert config["repo_url"] == "https://github.com/eamigo86/dj-hyperview"
    assert config["docs_dir"] == "docs"
    assert config["site_dir"] == "site"
    assert config["nav"] == [
        {"Home": "index.md"},
        {"Installation": "installation.md"},
        {"Configuration": "configuration.md"},
        {"Filesystem sources": "filesystem.md"},
        {"Database and admin": "database-admin.md"},
    ]
    assert [target for item in config["nav"] for target in item.values()] == list(DOCS)


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


def test_documentation_declares_foundation_limits_and_valid_local_links() -> None:
    """The foundation stays package-owned and does not promise future guides."""
    pages = _documentation_pages()

    assert "does not ship application screens" in pages["index.md"]
    assert "[Use filesystem sources](filesystem.md)" in pages["index.md"]
    for source in DOCS:
        for target in DOCS:
            if f"]({target})" in pages[source]:
                assert (ROOT / "docs" / target).is_file()
