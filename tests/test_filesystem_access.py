"""Filesystem errors must retain their meaning across Python versions."""

import os
from errno import EACCES, EIO, ELOOP, ENAMETOOLONG, ENOENT, ENOTDIR
from pathlib import Path

import pytest

from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.sources import FileSystemSource


@pytest.mark.parametrize("error_number", [EACCES, EIO])
def test_stat_errors_cannot_be_misreported_as_missing_files(
    error_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use stat explicitly rather than Python 3.14's error-suppressing is_file."""
    candidate = tmp_path / "screen.xml"
    candidate.write_text("<view />", encoding="utf-8")
    original_stat = Path.stat

    def checked_stat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path == candidate:
            raise OSError(error_number, "sensitive storage failure", str(candidate))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", checked_stat)
    monkeypatch.setattr(Path, "is_file", lambda path: False)

    with pytest.raises(SourceUnavailable) as captured:
        FileSystemSource([tmp_path]).resolve("screen.xml")

    assert captured.value.reason == "read failed"
    assert captured.value.__cause__ is None
    assert "sensitive" not in str(captured.value)


@pytest.mark.parametrize("error_number", [ENOENT, ENOTDIR, ELOOP, ENAMETOOLONG])
def test_known_stat_misses_preserve_the_fallback_contract(
    error_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only known absent or unusable paths remain ordinary misses."""
    candidate = tmp_path / "screen.xml"
    original_stat = Path.stat

    def checked_stat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path == candidate:
            raise OSError(error_number, "unavailable", str(candidate))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", checked_stat)

    assert FileSystemSource([tmp_path]).resolve("screen.xml") is None


def test_unsearchable_directory_does_not_serve_a_lower_priority_template(
    tmp_path: Path,
) -> None:
    """An actual permission failure remains a typed error on Python 3.14."""
    private = tmp_path / "primary" / "private"
    fallback = tmp_path / "fallback" / "private"
    private.mkdir(parents=True)
    fallback.mkdir(parents=True)
    (private / "screen.xml").write_text("<view>primary</view>", encoding="utf-8")
    (fallback / "screen.xml").write_text("<view>fallback</view>", encoding="utf-8")
    source = FileSystemSource([private.parent, fallback.parent])
    permissions = private.stat().st_mode
    try:
        private.chmod(0)
        if os.access(private, os.X_OK):
            pytest.skip("The current user can bypass directory permission checks")
        with pytest.raises(SourceUnavailable, match="filesystem.*read failed"):
            source.resolve("private/screen.xml")
    finally:
        private.chmod(permissions)


def test_directory_is_not_a_regular_template_file(tmp_path: Path) -> None:
    """A directory with a template-like name remains a miss."""
    (tmp_path / "screen.xml").mkdir()

    assert FileSystemSource([tmp_path]).resolve("screen.xml") is None
