from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from tools.package_guard import main, validate_project, validate_wheel

PROJECT_ROOT = Path(__file__).parents[1]


def write_wheel(path: Path, *, distribution: str, members: dict[str, str]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr(
            f"{distribution.replace('-', '_')}-0.1.0.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {distribution}\nVersion: 0.1.0\n",
        )


def test_project_has_expected_identity_and_no_runtime_markup() -> None:
    assert validate_project(PROJECT_ROOT) == []


def test_project_reports_wrong_identity_and_runtime_markup(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "wrong-name"\n')
    package = tmp_path / "src" / "dj_hyperview"
    package.mkdir(parents=True)
    (package / "screen.hxml").write_text("<doc />")

    assert validate_project(tmp_path) == [
        "distribution must be 'dj-hyperview'",
        "runtime markup is forbidden: src/dj_hyperview/screen.hxml",
    ]


def test_wheel_has_expected_identity_and_no_runtime_markup(tmp_path: Path) -> None:
    wheel = tmp_path / "dj_hyperview-0.1.0-py3-none-any.whl"
    write_wheel(
        wheel,
        distribution="dj-hyperview",
        members={"dj_hyperview/__init__.py": ""},
    )

    assert validate_wheel(wheel) == []


def test_wheel_reports_wrong_identity_and_runtime_markup(tmp_path: Path) -> None:
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="wrong-name",
        members={
            "dj_hyperview/__init__.py": "",
            "dj_hyperview/screen.XML": "<doc />",
        },
    )

    assert validate_wheel(wheel) == [
        "distribution must be 'dj-hyperview'",
        "runtime markup is forbidden: dj_hyperview/screen.XML",
    ]


def test_wheel_cli_returns_failure_and_reports_violations(
    tmp_path: Path, capsys
) -> None:
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="wrong-name",
        members={"dj_hyperview/__init__.py": ""},
    )

    assert main([str(wheel)]) == 1
    assert capsys.readouterr().out == (
        f"{wheel}: distribution must be 'dj-hyperview'\n"
    )


def test_wheel_cli_accepts_valid_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "candidate.whl"
    write_wheel(
        wheel,
        distribution="dj-hyperview",
        members={"dj_hyperview/__init__.py": ""},
    )

    assert main([str(wheel)]) == 0
