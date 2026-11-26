"""The package ships Python code with the expected identity and no runtime markup."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from tools.package_guard import main, validate_project, validate_wheel

import dj_hyperview

PROJECT_ROOT = Path(__file__).parents[1]


def write_wheel(path: Path, *, distribution: str, members: dict[str, str]) -> None:
    """Write a wheel archive with the given members and a METADATA file.

    Args:
        path: Path of the wheel archive to write.
        distribution: Distribution name the METADATA file declares.
        members: Archive members, as a mapping from the member name to its text.
    """
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr(
            f"{distribution.replace('-', '_')}-0.1.0.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {distribution}\nVersion: 0.1.0\n",
        )


def test_project_has_expected_identity_and_no_runtime_markup() -> None:
    """Project has expected identity and no runtime markup."""
    assert validate_project(PROJECT_ROOT) == []
    assert (PROJECT_ROOT / "src" / "dj_hyperview" / "py.typed").is_file()
    try:
        expected_version = version("dj-hyperview")
    except PackageNotFoundError:
        metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
        expected_version = metadata["project"]["version"]
    assert dj_hyperview.__version__ == expected_version


def test_project_reports_wrong_identity_and_runtime_markup(tmp_path: Path) -> None:
    """Project reports wrong identity and runtime markup.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "wrong-name"\n')
    package = tmp_path / "src" / "dj_hyperview"
    package.mkdir(parents=True)
    (package / "py.typed").touch()
    (package / "screen.hxml").write_text("<doc />")

    assert validate_project(tmp_path) == [
        "distribution must be 'dj-hyperview'",
        "runtime markup is forbidden: src/dj_hyperview/screen.hxml",
    ]


def test_project_rejects_schema_files_outside_the_versioned_registry(
    tmp_path: Path,
) -> None:
    """Runtime XSD files must live inside the package-owned schema registry.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "dj-hyperview"\n')
    package = tmp_path / "src" / "dj_hyperview"
    package.mkdir(parents=True)
    (package / "py.typed").touch()
    (package / "screen.xsd").write_text("<schema />")

    assert validate_project(tmp_path) == [
        "schema resource must live below dj_hyperview/schemas: "
        "src/dj_hyperview/screen.xsd"
    ]


def test_wheel_has_expected_identity_and_no_runtime_markup(tmp_path: Path) -> None:
    """Wheel has expected identity and no runtime markup.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    wheel = tmp_path / "dj_hyperview-0.1.0-py3-none-any.whl"
    write_wheel(
        wheel,
        distribution="dj-hyperview",
        members={"dj_hyperview/__init__.py": "", "dj_hyperview/py.typed": ""},
    )

    assert validate_wheel(wheel) == []


def test_wheel_requires_the_pep_561_marker(tmp_path: Path) -> None:
    """Wheel requires the PEP 561 marker.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    wheel = tmp_path / "dj_hyperview-0.1.0-py3-none-any.whl"
    write_wheel(
        wheel,
        distribution="dj-hyperview",
        members={"dj_hyperview/__init__.py": ""},
    )

    assert validate_wheel(wheel) == ["wheel must contain dj_hyperview/py.typed"]


def test_wheel_reports_wrong_identity_and_runtime_markup(tmp_path: Path) -> None:
    """Wheel reports wrong identity and runtime markup.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="wrong-name",
        members={
            "dj_hyperview/__init__.py": "",
            "dj_hyperview/py.typed": "",
            "dj_hyperview/screen.XML": "<doc />",
        },
    )

    assert validate_wheel(wheel) == [
        "distribution must be 'dj-hyperview'",
        "runtime markup is forbidden: dj_hyperview/screen.XML",
    ]


def test_wheel_cli_returns_failure_and_reports_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wheel CLI returns failure and reports violations.

    Args:
        tmp_path: Temporary directory that holds the wheel.
        capsys: Captures the output printed by the wheel guard.
    """
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="wrong-name",
        members={"dj_hyperview/__init__.py": "", "dj_hyperview/py.typed": ""},
    )

    assert main([str(wheel)]) == 1
    assert capsys.readouterr().out == (
        f"{wheel}: distribution must be 'dj-hyperview'\n"
    )


def test_wheel_cli_accepts_valid_wheel(tmp_path: Path) -> None:
    """Wheel CLI accepts a valid wheel.

    Args:
        tmp_path: Temporary directory that holds the wheel.
    """
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="dj-hyperview",
        members={"dj_hyperview/__init__.py": "", "dj_hyperview/py.typed": ""},
    )

    assert main([str(wheel)]) == 0
