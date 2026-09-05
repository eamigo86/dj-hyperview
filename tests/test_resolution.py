import traceback
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview.exceptions import (
    HyperviewConfigurationError,
    InvalidTemplateName,
    SourceUnavailable,
    TemplateNotFound,
    TemplateValidationError,
)
from dj_hyperview.resolver import TemplateResolver, resolve_template
from dj_hyperview.sources import (
    FileSystemSource,
    ResolvedTemplate,
    TemplateSource,
    canonicalize_template_name,
)

UNSAFE_UNICODE_NAMES = (
    "screens/home\n.xml",
    "screens/home\t.xml",
    "screens/\x01home.xml",
    "screens/\x7fhome.xml",
    "screens/\x85home.xml",
    "screens/\ud800.xml",
    "screens/\udfff.xml",
)


@pytest.mark.parametrize(
    "name",
    [
        "screen.xml",
        "screens/home.hxml",
        "niñez.xml",
        "screens/e\u0301.xml",
        "screens/👩\u200d💻.xml",
    ],
)
def test_canonical_name_preserves_relative_posix_path(name):
    assert canonicalize_template_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        "/screen.xml",
        "C:/screen.xml",
        ".",
        "./screen.xml",
        "screens/../screen.xml",
        "screens//screen.xml",
        "screens/",
        "screens\\screen.xml",
        "screens/\0screen.xml",
        *UNSAFE_UNICODE_NAMES,
    ],
)
def test_canonical_name_rejects_unsafe_names(name):
    with pytest.raises(InvalidTemplateName, match="Invalid template name") as error:
        canonicalize_template_name(name)

    assert error.value.name == name
    assert str(error.value) == "Invalid template name"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    if isinstance(name, str) and name:
        assert name not in repr(error.value)


def test_canonical_name_error_traceback_redacts_name():
    sensitive_name = "../private-screen.xml"

    with pytest.raises(InvalidTemplateName) as captured:
        canonicalize_template_name(sensitive_name)

    rendered = "".join(traceback.format_exception(captured.value))
    assert sensitive_name not in rendered


def test_filesystem_source_uses_directories_in_order_and_utf8(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "screen.xml").write_text("<view>first</view>", encoding="utf-8")
    content = "<view>niñez</view>"
    (second / "screen.xml").write_text("<view>second</view>", encoding="utf-8")
    (second / "niñez.xml").write_text(content, encoding="utf-8")
    source = FileSystemSource([first, second])

    assert isinstance(source, TemplateSource)
    assert source.resolve("screen.xml").content == "<view>first</view>"
    resolved = source.resolve("niñez.xml")
    assert resolved == ResolvedTemplate(
        name="niñez.xml",
        content=content,
        origin=(second / "niñez.xml").as_uri(),
        source="filesystem",
        revision=sha256(content.encode()).hexdigest(),
    )
    with pytest.raises(FrozenInstanceError):
        resolved.content = "changed"


def test_filesystem_source_returns_none_on_miss(tmp_path):
    assert FileSystemSource([tmp_path]).resolve("missing.xml") is None


@pytest.mark.parametrize("template_dirs", ["/tmp/hyperview", b"/tmp/hyperview"])
def test_filesystem_source_rejects_scalar_template_roots(template_dirs) -> None:
    """A scalar root cannot be misinterpreted as an iterable of characters."""
    with pytest.raises(HyperviewConfigurationError, match="template_dirs"):
        FileSystemSource(template_dirs)


def test_filesystem_source_rejects_a_bare_path_root(tmp_path) -> None:
    """A Path must be wrapped in an ordered roots collection."""
    with pytest.raises(HyperviewConfigurationError, match="template_dirs"):
        FileSystemSource(tmp_path)


def test_filesystem_source_follows_a_configured_root_symlink_swap(tmp_path) -> None:
    """Long-lived source instances follow atomic release symlink changes."""
    first = tmp_path / "release-1"
    second = tmp_path / "release-2"
    first.mkdir()
    second.mkdir()
    (first / "screen.xml").write_text("<view>first</view>", encoding="utf-8")
    (second / "screen.xml").write_text("<view>second</view>", encoding="utf-8")
    current = tmp_path / "current"
    current.symlink_to(first, target_is_directory=True)
    source = FileSystemSource([current])

    assert source.resolve("screen.xml").content == "<view>first</view>"
    current.unlink()
    current.symlink_to(second, target_is_directory=True)

    assert source.resolve("screen.xml").content == "<view>second</view>"


def test_filesystem_source_treats_platform_path_limit_as_a_miss(tmp_path) -> None:
    """Host filesystem limits cannot leak an absolute path through a raw error."""
    source = FileSystemSource([tmp_path])

    assert source.resolve(f"{'a' * 300}.xml") is None


def test_filesystem_source_treats_a_symlink_loop_as_a_miss(tmp_path) -> None:
    """A looping entry behaves like an unavailable candidate."""
    loop = tmp_path / "loop.xml"
    loop.symlink_to(loop)

    assert FileSystemSource([tmp_path]).resolve("loop.xml") is None


def test_filesystem_source_maps_non_utf8_content_to_validation_error(tmp_path) -> None:
    """Stored bytes must satisfy the source's explicit UTF-8 contract."""
    (tmp_path / "screen.xml").write_bytes(b"<view>caf\xe9</view>")

    with pytest.raises(TemplateValidationError) as captured:
        FileSystemSource([tmp_path]).resolve("screen.xml")

    assert captured.value.code == "invalid_encoding"
    assert captured.value.__cause__ is None


def test_empty_template_is_a_hit_before_later_content(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "screen.xml").write_bytes(b"")
    (second / "screen.xml").write_text("fallback", encoding="utf-8")

    resolved = FileSystemSource([first, second]).resolve("screen.xml")

    assert resolved.content == ""
    assert resolved.origin == (first / "screen.xml").as_uri()
    assert resolved.revision == sha256(b"").hexdigest()


def test_filesystem_source_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.xml"
    secret.write_text("secret", encoding="utf-8")
    (root / "linked.xml").symlink_to(secret)

    with pytest.raises(InvalidTemplateName):
        FileSystemSource([root]).resolve("linked.xml")


def test_filesystem_symlink_rejection_redacts_exception_chain(tmp_path, monkeypatch):
    root = tmp_path / "private-root"
    outside = tmp_path / "private-outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "sensitive-screen.xml"
    secret.write_text("<view>private</view>", encoding="utf-8")
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    source = FileSystemSource([root])
    requested_name = f"{link.name}/{secret.name}"

    def fail_if_read(_path):
        raise AssertionError("outside template must not be read")

    monkeypatch.setattr(Path, "read_bytes", fail_if_read)
    with pytest.raises(InvalidTemplateName) as captured:
        source.resolve(requested_name)

    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None
    for sensitive in (
        requested_name,
        secret.name,
        root.name,
        outside.name,
        str(root),
        str(outside),
    ):
        assert sensitive not in str(error)
        assert sensitive not in repr(error)
        assert sensitive not in rendered


def test_filesystem_source_redacts_path_resolution_errors(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    source = FileSystemSource([root])
    failure = OSError(f"storage unavailable at {root}")

    def fail_resolve(_path):
        raise failure

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(SourceUnavailable) as captured:
        source.resolve("screen.xml")

    assert captured.value.source == "filesystem"
    assert captured.value.reason == "read failed"
    assert captured.value.__cause__ is None
    assert str(root) not in str(captured.value)


def test_filesystem_source_redacts_file_read_errors(tmp_path, monkeypatch):
    """File access failures expose only the stable source error contract."""
    path = tmp_path / "screen.xml"
    path.write_text("<view />", encoding="utf-8")

    def fail_read(_path):
        raise PermissionError(f"permission denied: {path}")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(SourceUnavailable) as captured:
        FileSystemSource([tmp_path]).resolve("screen.xml")

    assert captured.value.source == "filesystem"
    assert captured.value.reason == "read failed"
    assert captured.value.__cause__ is None
    assert str(path) not in str(captured.value)


def test_resolver_preserves_source_unavailable():
    failure = SourceUnavailable("filesystem", "storage unavailable")

    class FailingSource:
        def resolve(self, name):
            raise failure

    with pytest.raises(SourceUnavailable) as captured:
        TemplateResolver([FailingSource()]).resolve("screen.xml")

    assert captured.value is failure


class RecordingSource:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def resolve(self, name):
        self.calls.append(name)
        return self.result


@pytest.mark.parametrize("name", UNSAFE_UNICODE_NAMES)
def test_resolver_rejects_unsafe_unicode_name_before_source_lookup(name):
    source = RecordingSource()

    with pytest.raises(InvalidTemplateName) as captured:
        TemplateResolver([source]).resolve(name)

    assert str(captured.value) == "Invalid template name"
    assert source.calls == []


def test_resolver_uses_first_match_and_skips_misses():
    resolved = ResolvedTemplate("screen.xml", "winner", "memory:screen", "test", "1")
    miss, winner, later = (
        RecordingSource(),
        RecordingSource(resolved),
        RecordingSource(resolved),
    )

    assert TemplateResolver([miss, winner, later]).resolve("screen.xml") is resolved
    assert [source.calls for source in (miss, winner, later)] == [
        ["screen.xml"],
        ["screen.xml"],
        [],
    ]


def test_resolver_raises_typed_error_after_all_sources_miss():
    with pytest.raises(
        TemplateNotFound, match="Template not found: missing.xml"
    ) as error:
        TemplateResolver([RecordingSource()]).resolve("missing.xml")

    assert error.value.name == "missing.xml"


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": "configured", "revision": "settings-r1"},
            }
        ]
    }
)
def test_resolver_imports_configured_backend_with_options():
    resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert (resolved.content, resolved.revision) == ("configured", "settings-r1")


def test_configured_filesystem_source_uses_template_dirs(tmp_path):
    (tmp_path / "screen.xml").write_text("from settings", encoding="utf-8")
    configured = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    }

    with override_settings(HYPERVIEW=configured):
        assert resolve_template("screen.xml").content == "from settings"


def test_resolution_contract_is_exported_from_package_root():
    import dj_hyperview

    expected = {
        "FileSystemSource": FileSystemSource,
        "InvalidTemplateName": InvalidTemplateName,
        "ResolvedTemplate": ResolvedTemplate,
        "TemplateNotFound": TemplateNotFound,
        "TemplateResolver": TemplateResolver,
        "TemplateSource": TemplateSource,
        "resolve_template": resolve_template,
    }
    assert {name: getattr(dj_hyperview, name) for name in expected} == expected
