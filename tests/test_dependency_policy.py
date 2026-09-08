"""Dependency metadata policy tests."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
LOCKED_DIRECT = {
    "coverage": "7.16.0",
    "django": "6.1.1",
    "django-ace": "1.44.0",
    "lxml": "6.1.3",
    "packaging": "26.3",
    "pytest": "9.1.1",
    "pytest-cov": "7.1.0",
    "pytest-django": "4.14.0",
    "redis": "8.1.0",
    "ruff": "0.16.6",
    "uv-build": "0.12.9",
    "xmlschema": "4.3.2",
    "zensical": "0.0.59",
}


def test_manifest_declares_audited_compatible_ranges() -> None:
    """Runtime and tooling metadata encode the dated compatibility policy."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert metadata["project"]["requires-python"] == ">=3.12,<3.15"
    assert metadata["project"]["dependencies"] == [
        "Django>=5.2,<6.2",
        "lxml>=6.1.3,<7",
    ]
    assert metadata["project"]["optional-dependencies"] == {
        "schema": ["xmlschema>=4.3,<5"],
        "editor": ["django-ace>=1.44,<2", "xmlschema>=4.3,<5"],
    }
    assert metadata["project"]["urls"] == {
        "Documentation": "https://eamigo86.github.io/dj-hyperview/",
        "Release notes": "https://eamigo86.github.io/dj-hyperview/changelog/",
        "Source": "https://github.com/eamigo86/dj-hyperview",
    }
    assert metadata["build-system"]["requires"] == ["uv_build>=0.12.9,<0.13"]
    assert metadata["dependency-groups"]["dev"] == [
        "coverage[toml]>=7.16,<8",
        "django-ace>=1.44,<2",
        "packaging>=26.3,<27",
        "pytest>=9.1,<10",
        "pytest-cov>=7.1,<8",
        "pytest-django>=4.14,<5",
        "ruff>=0.16.6,<0.17",
        "uv-build>=0.12.9,<0.13",
        "xmlschema>=4.3,<5",
        "zensical==0.0.59",
    ]
    assert metadata["dependency-groups"]["redis"] == ["redis==8.1.0"]
    assert metadata["tool"]["dj-hyperview"] == {
        "dependency-audit-date": "2026-09-07",
        "supported-python": ["3.12", "3.13", "3.14"],
        "supported-django": ["5.2.17", "6.1.1"],
    }
    manifest = (ROOT / "pyproject.toml").read_text()
    assert "django-hyperview" not in manifest
    assert "django_hv" not in manifest


def test_manifest_and_repository_declare_the_mit_license() -> None:
    """Modern package metadata and the repository expose the selected license."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    license_text = (ROOT / "LICENSE").read_text()
    readme = (ROOT / "README.md").read_text()

    assert metadata["project"]["license"] == "MIT"
    assert metadata["project"]["license-files"] == ["LICENSE"]
    assert license_text.startswith(
        "MIT License\n\nCopyright (c) 2026 Ernesto Perez Amigo\n"
    )
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text
    assert "img.shields.io/pypi/l/dj-hyperview" in readme
    assert "## License" in readme
    assert (
        "[MIT License](https://github.com/eamigo86/dj-hyperview/blob/main/LICENSE)"
        in readme
    )


def test_versioned_lock_pins_audited_tools_without_legacy_names() -> None:
    """The repository lock fixes audited versions and excludes legacy packages."""
    assert "uv.lock" not in (ROOT / ".gitignore").read_text().splitlines()

    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = {package["name"]: package["version"] for package in lock["package"]}
    assert {name: versions[name] for name in LOCKED_DIRECT} == LOCKED_DIRECT

    manifests = (ROOT / "pyproject.toml").read_text() + (ROOT / "uv.lock").read_text()
    assert "django-hyperview" not in manifests
    assert "django_hv" not in manifests
