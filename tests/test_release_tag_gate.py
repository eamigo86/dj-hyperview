"""Release tag and project-version gate tests."""

import runpy
import subprocess
import sys
import tomllib
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from packaging.version import Version

ROOT = Path(__file__).parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_normal_ci_pushes_exclude_tags() -> None:
    """Normal CI handles branches while release tags use one workflow."""
    workflow = yaml.load(CI_WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    assert workflow["on"]["push"] == {"branches": ["**"]}


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("1.2.3", "v1.2.3"),
        ("0.1.0a1", "v0.1.0a1"),
        ("0.1.0b1", "v0.1.0b1"),
        ("0.1.0b2", "v0.1.0b2"),
        ("1!2.0.post1+linux.1", "v1!2.0.post1+linux.1"),
    ],
)
def test_release_tag_must_exactly_match_project_version(
    tmp_path: Path, version: str, tag: str
) -> None:
    """Exact stable and prerelease project versions accept one tag."""
    project_file = _project_file(tmp_path, version)

    assert _release_module().main([tag], project_file=project_file) == 0


@pytest.mark.parametrize(
    "version",
    [
        "banana.1.2",
        "not-a.1.0",
        "1..2.3",
        "01.2.3",
        "1.0-1",
        "1.0.0+ABC",
        " 1.2.3",
        "1.2.3 ",
    ],
)
def test_release_tag_rejects_invalid_or_noncanonical_versions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    version: str,
) -> None:
    """Invalid and normalized project versions fail before release."""
    project_file = _project_file(tmp_path, version)

    status = _release_module().main([f"v{version}"], project_file=project_file)

    assert status == 2
    assert capsys.readouterr().err == "Project version metadata is unavailable.\n"


def test_release_tag_mismatch_fails_without_metadata_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mismatched tag fails with one stable public diagnostic."""
    project_file = _project_file(tmp_path, "1.2.3")

    status = _release_module().main(["v1.2.4"], project_file=project_file)

    assert status == 1
    assert capsys.readouterr().err == "Release tag does not match project version.\n"


def test_release_tag_requires_exactly_one_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing or extra tag values fail before metadata inspection."""
    missing = tmp_path / "private-project.toml"

    status = _release_module().main([], project_file=missing)

    assert status == 2
    assert capsys.readouterr().err == "Release tag validation requires one tag.\n"


@pytest.mark.parametrize(
    "content",
    [
        'project = "not-a-table"\n',
        "[project]\n",
        "[project]\nversion = 7\n",
        "[project\n",
    ],
)
def test_release_tag_rejects_unusable_project_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    """Missing, mistyped, and malformed versions fail with one message."""
    project_file = tmp_path / "private-project.toml"
    project_file.write_text(content)

    status = _release_module().main(["v1.2.3"], project_file=project_file)

    assert status == 2
    assert capsys.readouterr().err == "Project version metadata is unavailable.\n"


def test_release_tag_module_entrypoint_uses_process_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module entrypoint validates the tag supplied by the runner."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tag = f"v{project['project']['version']}"
    monkeypatch.setattr(sys, "argv", ["check_release_tag", tag])

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(
            str(ROOT / "tools" / "check_release_tag.py"), run_name="__main__"
        )

    assert caught.value.code == 0


def test_release_tag_gate_finds_the_project_from_any_working_directory(
    tmp_path: Path,
) -> None:
    """The tool reads its own repository's metadata, not the current directory's."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tag = f"v{project['project']['version']}"
    script = ROOT / "tools" / "check_release_tag.py"

    result = subprocess.run(
        [sys.executable, str(script), tag], cwd=tmp_path, capture_output=True
    )

    assert (result.returncode, result.stderr) == (0, b"")


def test_repository_release_version_is_synchronized() -> None:
    """Project metadata and lock expose one release version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    project_version = project["project"]["version"]
    locked_project = next(
        package for package in lock["package"] if package["name"] == "dj-hyperview"
    )

    assert locked_project["version"] == project_version


def _release_module() -> ModuleType:
    """Import the release gate only when a test invokes its behavior."""
    return import_module("tools.check_release_tag")


def _project_file(tmp_path: Path, version: str) -> Path:
    """Write minimal project metadata for one release-gate scenario."""
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "example"\nversion = "{version}"\n')
    return path


def _allowed_maturities(version: Version) -> set[str]:
    """Return the Development Status values a release may carry.

    A prerelease, a development build included, must not claim more than its
    stage. A final release in the 0.x line may still call itself Beta; from 1.0 on
    it is Production/Stable.
    """
    if version.is_prerelease:
        stage = version.pre[0] if version.pre else "dev"
        return {
            "dev": {"2 - Pre-Alpha"},
            "a": {"3 - Alpha"},
            "b": {"4 - Beta"},
            "rc": {"4 - Beta"},
        }[stage]
    if version.major == 0:
        return {"4 - Beta", "5 - Production/Stable"}
    return {"5 - Production/Stable"}


@pytest.mark.parametrize(
    ("version", "allowed"),
    [
        ("1.0.0.dev1", {"2 - Pre-Alpha"}),
        ("1.0.0a1", {"3 - Alpha"}),
        ("1.0.0b1", {"4 - Beta"}),
        ("1.0.0rc1", {"4 - Beta"}),
        ("1.0.0b1.dev2", {"4 - Beta"}),
        ("1.0.0", {"5 - Production/Stable"}),
        ("1.0.0.post1", {"5 - Production/Stable"}),
        ("0.1.0", {"4 - Beta", "5 - Production/Stable"}),
    ],
)
def test_release_maturity_follows_the_version_stage(
    version: str, allowed: set[str]
) -> None:
    """A development build is a prerelease too, and a 0.x final may stay Beta."""
    assert _allowed_maturities(Version(version)) == allowed


def test_development_status_classifier_matches_the_release_maturity() -> None:
    """A prerelease must not claim production-stable metadata, nor the reverse."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = [
        classifier.removeprefix("Development Status :: ")
        for classifier in project["classifiers"]
        if classifier.startswith("Development Status ::")
    ]

    assert len(declared) == 1
    assert declared[0] in _allowed_maturities(Version(project["version"]))
