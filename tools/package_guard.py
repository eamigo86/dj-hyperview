from __future__ import annotations

import argparse
import tomllib
from collections.abc import Iterable, Sequence
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from zipfile import ZipFile

EXPECTED_DISTRIBUTION = "dj-hyperview"
EXPECTED_PACKAGE = "dj_hyperview"
RUNTIME_MARKUP_SUFFIXES = frozenset({".hxml", ".xml"})


def _runtime_markup_violations(names: Iterable[str]) -> list[str]:
    return [
        f"runtime markup is forbidden: {name}"
        for name in names
        if Path(name).suffix.lower() in RUNTIME_MARKUP_SUFFIXES
    ]


def validate_project(root: Path) -> list[str]:
    """Return package-boundary violations for a source checkout."""
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    violations: list[str] = []

    if metadata["project"]["name"] != EXPECTED_DISTRIBUTION:
        violations.append(f"distribution must be {EXPECTED_DISTRIBUTION!r}")

    package_root = root / "src" / EXPECTED_PACKAGE
    if not package_root.is_dir():
        violations.append(f"package must be src/{EXPECTED_PACKAGE}")

    package_files = (
        (
            path.relative_to(root).as_posix()
            for path in package_root.rglob("*")
            if path.is_file()
        )
        if package_root.is_dir()
        else ()
    )
    violations.extend(_runtime_markup_violations(package_files))

    return violations


def validate_wheel(path: Path) -> list[str]:
    """Return package-boundary violations for a wheel archive."""
    violations: list[str] = []
    with ZipFile(path) as archive:
        names = archive.namelist()
        metadata_files = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]

        if len(metadata_files) != 1:
            violations.append("wheel must contain exactly one METADATA file")
        else:
            metadata = BytesParser(policy=default).parsebytes(
                archive.read(metadata_files[0])
            )
            if metadata["Name"] != EXPECTED_DISTRIBUTION:
                violations.append(f"distribution must be {EXPECTED_DISTRIBUTION!r}")

        package_prefix = f"{EXPECTED_PACKAGE}/"
        if not any(name.startswith(package_prefix) for name in names):
            violations.append(f"wheel must contain package {EXPECTED_PACKAGE!r}")

        violations.extend(_runtime_markup_violations(names))

    return violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate dj-hyperview wheels")
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args(argv)

    failed = False
    for wheel in args.wheels:
        for violation in validate_wheel(wheel):
            failed = True
            print(f"{wheel}: {violation}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
