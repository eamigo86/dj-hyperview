"""Validate a release tag against package project metadata."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from packaging.version import InvalidVersion, Version

PROJECT_FILE = Path("pyproject.toml")


def _project_version(project_file: Path) -> str | None:
    """Return a usable project version or fail closed."""
    try:
        project = tomllib.loads(project_file.read_text()).get("project")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    if not isinstance(version, str) or not version or version.strip() != version:
        return None
    try:
        parsed = Version(version)
    except InvalidVersion:
        return None
    return version if str(parsed) == version else None


def main(
    argv: Sequence[str] | None = None, *, project_file: Path = PROJECT_FILE
) -> int:
    """Check that one release tag exactly matches the project version.

    Args:
        argv: Optional command arguments containing the release tag.
        project_file: Project metadata file to inspect.

    Returns:
        Zero for an exact match and a nonzero status otherwise.
    """
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("Release tag validation requires one tag.", file=sys.stderr)
        return 2
    version = _project_version(project_file)
    if version is None:
        print("Project version metadata is unavailable.", file=sys.stderr)
        return 2
    if arguments[0] != f"v{version}":
        print("Release tag does not match project version.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
