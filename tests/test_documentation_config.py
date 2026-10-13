"""Documentation facts checked against the site config, the code and CI."""

import inspect
import re
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest
import xmlschema
import yaml

from dj_hyperview import __all__ as public_api
from dj_hyperview.conf import DEFAULTS
from dj_hyperview.contrib.database.sources import DatabaseSource
from dj_hyperview.sources import FileSystemSource
from tests.settings import DATABASES

ROOT = Path(__file__).parents[1]
DOCS = ROOT / "docs"
SITE = "https://eamigo86.github.io/dj-hyperview/"
# Rows of the settings reference that are not fields of the settings dataclasses.
UNMAPPED_SETTING_ROWS = {
    "HYPERVIEW",
    "SOURCES[].BACKEND",
    "SOURCES[].OPTIONS",
    "REALTIME.REDIS_URL",
    "REALTIME.NAMESPACE",
}


def _public_markdown_paths() -> tuple[Path, ...]:
    """Return the published Markdown pages, without private development notes."""
    return tuple(
        path
        for path in sorted(DOCS.rglob("*.md"))
        if not path.is_relative_to(DOCS / "development")
    )


def _navigation_targets(items: list[dict[str, object]]) -> list[str]:
    """Return the leaf page paths of a nested Zensical navigation."""
    targets: list[str] = []
    for item in items:
        for value in item.values():
            if isinstance(value, str):
                targets.append(value)
            elif isinstance(value, list):
                targets.extend(_navigation_targets(value))
    return targets


def test_navigation_lists_every_public_page_exactly_once() -> None:
    config = yaml.safe_load((ROOT / "zensical.yml").read_text())
    targets = _navigation_targets(config["nav"])

    assert len(targets) == len(set(targets))
    assert set(targets) == {
        str(path.relative_to(DOCS)) for path in _public_markdown_paths()
    }


def test_compatibility_page_describes_the_active_contract() -> None:
    from dj_hyperview import HYPERVIEW_SCHEMA_VERSION
    from dj_hyperview._schema_revision import SCHEMA_REVISION

    page = (DOCS / "hyperview-compatibility.md").read_text()

    assert f"Hyperview {HYPERVIEW_SCHEMA_VERSION}" in page
    assert f"`{SCHEMA_REVISION}`" in page


def test_custom_component_schema_example_is_valid_xsd_1_1() -> None:
    schema = xmlschema.XMLSchema11(DOCS / "examples" / "hypertodo.xsd", allow="local")

    assert {"swipe-row", "swipe-action"} <= set(schema.elements)


def test_api_reference_names_every_public_export() -> None:
    reference = (DOCS / "api-reference.md").read_text()

    assert [name for name in public_api if f"`{name}`" not in reference] == []


def test_settings_reference_documents_exactly_the_supported_keys() -> None:
    page = (DOCS / "configuration.md").read_text()
    section = page.split("## Settings reference", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\| `([A-Z_.\[\]]+)` \|", section, re.M))
    supported = {field.name.upper() for field in fields(DEFAULTS)} | {
        f"{name.upper()}.{field.name.upper()}"
        for name in ("cache", "validation", "admin")
        for field in fields(getattr(DEFAULTS, name))
    }

    assert documented == supported | UNMAPPED_SETTING_ROWS


@pytest.mark.parametrize("source", [FileSystemSource, DatabaseSource])
def test_source_options_reference_covers_every_constructor_parameter(
    source: type,
) -> None:
    page = (DOCS / "configuration.md").read_text()
    parameters = set(inspect.signature(source).parameters)

    assert [name for name in parameters if f"| `{name}` |" not in page] == []


def test_markdown_links_remain_inside_the_zensical_document_tree() -> None:
    """Local Markdown targets stay publishable by the strict site build."""
    docs_root = DOCS.resolve()

    for source in _public_markdown_paths():
        for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]+)?\)", source.read_text()):
            if target.startswith(("https://", "http://")):
                continue
            resolved = (source.parent / target).resolve()
            assert resolved.is_relative_to(docs_root), (source, target)
            assert resolved.is_file(), (source, target)


def test_repository_links_point_at_files_that_exist() -> None:
    prefix = "https://github.com/eamigo86/dj-hyperview/blob/main/"
    pages = [ROOT / "README.md", *_public_markdown_paths()]
    targets = {
        target
        for page in pages
        for target in re.findall(re.escape(prefix) + r"([^)\s#\"']+)", page.read_text())
    }

    assert targets
    assert [target for target in targets if not (ROOT / target).is_file()] == []


@pytest.mark.parametrize(
    "contract",
    ["Python", "Django", "Database engines", "Editor JavaScript", "Redis client"],
)
def test_documented_verification_scope_matches_configured_profiles(
    contract: str,
) -> None:
    """Compare published scope rows with executable matrix and database settings."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    matrix = workflow["jobs"]["compatibility"]["strategy"]["matrix"]
    node = next(
        step["with"]["node-version"]
        for step in workflow["jobs"]["quality"]["steps"]
        if step.get("uses", "").startswith("actions/setup-node@")
    )
    expected = {
        "Python": metadata["tool"]["dj-hyperview"]["supported-python"],
        "Django": metadata["tool"]["dj-hyperview"]["supported-django"],
        "Database engines": sorted({value["ENGINE"] for value in DATABASES.values()}),
        "Editor JavaScript": [node],
        # Only the locked "redis" group is installed for the live Redis job.
        "Redis client": [
            requirement.removeprefix("redis==")
            for requirement in metadata["dependency-groups"]["redis"]
        ],
    }
    assert matrix["python"] == expected["Python"]
    assert matrix["django"] == expected["Django"]
    rows = {
        columns[0].strip(): re.findall(r"`([^`]+)`", columns[1])
        for line in (DOCS / "testing.md").read_text().splitlines()
        if line.startswith("| ") and len(columns := line.strip("|").split("|")) == 3
    }
    assert rows.get(contract) == expected[contract]


@pytest.mark.parametrize("entry", ["README.md", "docs/index.md", "docs/roadmap.md"])
def test_beta_entrypoints_link_to_existing_canonical_contract_sections(
    entry: str,
) -> None:
    """Readers can reach the compatibility commitment and its measured limits."""
    source = ROOT / entry
    text = source.read_text()
    for filename, anchor, heading in (
        (
            "api-reference.md",
            "beta-compatibility-policy",
            "## Beta compatibility policy",
        ),
        ("testing.md", "verified-scope", "## Verified scope"),
    ):
        if source.parent == ROOT:
            # The README is the PyPI long description, so it links by absolute URL.
            page = filename.removesuffix(".md")
            assert f"]({SITE}{page}/#{anchor})" in text
        else:
            assert f"]({filename}#{anchor})" in text
        assert heading in (DOCS / filename).read_text().splitlines()


def test_readme_links_are_absolute_because_pypi_renders_it_elsewhere() -> None:
    """A relative README link resolves under pypi.org and breaks."""
    readme = (ROOT / "README.md").read_text()

    relative = [
        target
        for target in re.findall(r"\]\(([^)\s]+)\)", readme)
        if not target.startswith(("https://", "http://", "#"))
    ]
    assert relative == []
    images = re.findall(r"src=[\"']([^\"']+)[\"']", readme)
    assert all(image.startswith("https://") for image in images)


def test_readme_editor_snippet_defines_the_permission_it_configures() -> None:
    """Copying the editor example must pass the ADMIN.PERMISSION system check."""
    from django.test import override_settings

    from dj_hyperview.checks import check_hyperview_settings

    readme = (ROOT / "README.md").read_text()
    block = next(
        block
        for block in re.findall(r"```python\n(.*?)```", readme, flags=re.DOTALL)
        if '"EDITOR": True' in block
    )
    namespace: dict[str, object] = {}
    exec(compile(block, "README.md", "exec"), namespace)

    with override_settings(HYPERVIEW=namespace["HYPERVIEW"]):
        identifiers = {message.id for message in check_hyperview_settings()}
    assert "dj_hyperview.E017" not in identifiers
