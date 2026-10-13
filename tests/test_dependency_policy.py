"""Dependency metadata policy tests.

The tests state how pyproject.toml, uv.lock and the supported-version policy
relate to each other. None repeats a version number, so a dependency bump or a
`uv lock --upgrade` inside the declared ranges changes no test.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).parents[1]
METADATA = tomllib.loads((ROOT / "pyproject.toml").read_text())
POLICY = METADATA["tool"]["dj-hyperview"]
DJANGO = next(
    Requirement(text)
    for text in METADATA["project"]["dependencies"]
    if canonicalize_name(Requirement(text).name) == "django"
)


def _declared_requirements() -> list[str]:
    declared = list(METADATA["project"]["dependencies"])
    declared += [
        requirement
        for group in METADATA["project"]["optional-dependencies"].values()
        for requirement in group
    ]
    declared += [
        requirement
        for group in METADATA["dependency-groups"].values()
        for requirement in group
    ]
    return declared + METADATA["build-system"]["requires"]


def test_locked_versions_satisfy_every_declared_requirement() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked: dict[str, list[str]] = {}
    for package in lock["package"]:
        locked.setdefault(package["name"], []).append(package["version"])

    unmet = []
    for text in _declared_requirements():
        requirement = Requirement(text)
        versions = locked.get(canonicalize_name(requirement.name), [])
        if not any(
            requirement.specifier.contains(version, prereleases=True)
            for version in versions
        ):
            unmet.append(text)

    assert unmet == []


def test_uv_lock_is_versioned() -> None:
    assert "uv.lock" not in (ROOT / ".gitignore").read_text().splitlines()


def test_requires_python_declares_no_upper_bound() -> None:
    """Newer interpreters are never blocked by metadata; CI defines what is tested."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    requires = SpecifierSet(METADATA["project"]["requires-python"])

    assert {specifier.operator for specifier in requires} == {">="}
    assert "3.15.0" in requires
    assert SpecifierSet(lock["requires-python"]) == requires


def test_classifiers_list_exactly_the_tested_pythons() -> None:
    """The tested interpreter set lives in classifiers, not in a version cap."""
    prefix = "Programming Language :: Python :: "
    classified = [
        classifier.removeprefix(prefix)
        for classifier in METADATA["project"]["classifiers"]
        if classifier.startswith(prefix)
    ]

    assert classified == POLICY["supported-python"]
    assert all(
        version in SpecifierSet(METADATA["project"]["requires-python"])
        for version in POLICY["supported-python"]
    )


def test_classifiers_list_exactly_the_tested_djangos() -> None:
    prefix = "Framework :: Django :: "
    classified = [
        classifier.removeprefix(prefix)
        for classifier in METADATA["project"]["classifiers"]
        if classifier.startswith(prefix)
    ]

    assert classified == [
        ".".join(version.split(".")[:2]) for version in POLICY["supported-django"]
    ]
    assert all(
        DJANGO.specifier.contains(version) for version in POLICY["supported-django"]
    )


def test_django_range_spans_exactly_the_tested_minors() -> None:
    """The range opens at the oldest tested minor and closes at the next one."""
    tested = sorted(Version(version) for version in POLICY["supported-django"])
    oldest, newest = tested[0], tested[-1]
    bounds = {specifier.operator: specifier.version for specifier in DJANGO.specifier}

    assert bounds == {
        ">=": f"{oldest.major}.{oldest.minor}",
        "<": f"{newest.major}.{newest.minor + 1}",
    }


def test_manifest_and_repository_declare_the_mit_license() -> None:
    license_text = (ROOT / "LICENSE").read_text()

    assert METADATA["project"]["license"] == "MIT"
    assert METADATA["project"]["license-files"] == ["LICENSE"]
    assert license_text.startswith("MIT License\n")
    assert "Permission is hereby granted, free of charge" in license_text
