"""The public cacheable attribute is the only way a source opts in to caching."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.contrib.database.sources import DatabaseSource
from dj_hyperview.resolver import TemplateResolver, resolve_template
from dj_hyperview.sources import FileSystemSource, ResolvedTemplate, TemplateSource

ROOT = Path(__file__).parents[1]
LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-cacheable",
    }
}
FILESYSTEM = "dj_hyperview.sources.FileSystemSource"
_TENANT: ContextVar[str] = ContextVar("tenant")


@contextmanager
def as_tenant(name: str) -> Iterator[None]:
    token = _TENANT.set(name)
    try:
        yield
    finally:
        _TENANT.reset(token)


class TenantSource:
    """Resolves content from ambient request state, as a multi-tenant source can."""

    def resolve(self, name: str) -> ResolvedTemplate:
        content = f"<view>{_TENANT.get()}</view>"
        return ResolvedTemplate(name, content, "tenant", "tenant", "1")


class OptedInTenantSource(TenantSource):
    cacheable = True


class TenantFileSource(FileSystemSource):
    """A filesystem subclass whose output no longer depends only on its roots."""

    cacheable = False


class WithdrawnDatabaseSource(DatabaseSource):
    """A database subclass that withdraws the inherited declaration."""

    cacheable = False


class PlainFileSource(FileSystemSource):
    """A filesystem subclass that keeps the built-in resolve."""


class PlainDatabaseSource(DatabaseSource):
    """A database subclass that keeps the built-in resolve."""


class OverridingFileSource(FileSystemSource):
    """A filesystem subclass whose resolve can read anything, such as a tenant."""

    def resolve(self, name: str) -> ResolvedTemplate | None:
        return super().resolve(name)


class OptedInFileSource(OverridingFileSource):
    cacheable = True


class OverridingDatabaseSource(DatabaseSource):
    """A database subclass whose resolve can read anything, such as a tenant."""

    def resolve(self, name: str) -> ResolvedTemplate | None:
        return super().resolve(name)


class OptedInDatabaseSource(OverridingDatabaseSource):
    cacheable = True


class AssigningFileSource(OverridingFileSource):
    """The instance-attribute form of the declaration, set in the constructor."""

    def __init__(self, *, declared: bool) -> None:
        super().__init__(template_dirs=())
        self.cacheable = declared


class AssigningDatabaseSource(DatabaseSource):
    """The instance-attribute form on a source that keeps the built-in resolve."""

    def __init__(self, *, using: str | None, declared: bool) -> None:
        super().__init__(using=using)
        self.cacheable = declared


@pytest.fixture(autouse=True)
def configured_cache() -> Iterator[None]:
    with override_settings(CACHES=LOCMEM_CACHES):
        caches["screens"].clear()
        yield


def _config(backend: str, namespace: str, **extra: object) -> dict[str, object]:
    return {
        "SOURCES": [{"BACKEND": backend}],
        "CACHE": {"ALIAS": "screens", "NAMESPACE": namespace},
        **extra,
    }


def test_custom_source_is_not_shared_between_tenants_by_default() -> None:
    """A source that never declared cacheable cannot leak one tenant's content."""
    config = _config(f"{__name__}.TenantSource", "tenant-default")
    with override_settings(HYPERVIEW=config):
        with as_tenant("a"):
            assert resolve_template("screen.xml").content == "<view>a</view>"
        with as_tenant("b"):
            assert resolve_template("screen.xml").content == "<view>b</view>"


def test_custom_source_is_cached_once_it_opts_in() -> None:
    """Declaring cacheable is a promise that output depends on name and options."""
    config = _config(f"{__name__}.OptedInTenantSource", "tenant-opted-in")
    with override_settings(HYPERVIEW=config):
        with as_tenant("a"):
            assert resolve_template("screen.xml").content == "<view>a</view>"
        with as_tenant("b"):
            assert resolve_template("screen.xml").content == "<view>a</view>"


def test_undeclared_source_never_touches_the_cache_backend() -> None:
    """No generation or entry is created for a source without the opt-in."""
    cache = TemplateCache("undeclared", alias="screens")
    resolver = TemplateResolver([TenantSource()], cache=cache)

    with (
        patch.object(cache.backend, "get", wraps=cache.backend.get) as read,
        patch.object(cache.backend, "add", wraps=cache.backend.add) as add,
        patch.object(cache.backend, "set", wraps=cache.backend.set) as write,
        as_tenant("a"),
    ):
        resolver.resolve("screen.xml")

    assert (read.call_count, add.call_count, write.call_count) == (0, 0, 0)


def test_builtin_sources_declare_their_cacheability() -> None:
    assert FileSystemSource(template_dirs=()).cacheable is True
    assert DatabaseSource(using="default").cacheable is True
    assert DatabaseSource(using=None).cacheable is False
    assert DatabaseSource().cacheable is False


def test_database_subclass_class_attribute_overrides_the_derived_value() -> None:
    assert WithdrawnDatabaseSource(using="default").cacheable is False


def test_subclass_that_keeps_the_builtin_resolve_keeps_the_default() -> None:
    assert PlainFileSource(template_dirs=()).cacheable is True
    assert PlainDatabaseSource(using="default").cacheable is True
    assert PlainDatabaseSource().cacheable is False


def test_subclass_that_overrides_resolve_must_opt_in_to_caching() -> None:
    """Overriding resolve may add ambient state, so the default is withdrawn."""
    assert OverridingFileSource(template_dirs=()).cacheable is False
    assert OverridingDatabaseSource(using="default").cacheable is False


def test_subclass_that_overrides_resolve_opts_in_with_a_class_attribute() -> None:
    assert OptedInFileSource(template_dirs=()).cacheable is True
    assert OptedInDatabaseSource(using="default").cacheable is True


@pytest.mark.parametrize("declared", [True, False])
def test_builtin_source_subclass_can_assign_the_instance_attribute(
    declared: bool,
) -> None:
    """The documented instance-attribute form works on the built-in sources."""
    assert AssigningFileSource(declared=declared).cacheable is declared
    routed = AssigningDatabaseSource(using="default", declared=declared)
    unrouted = AssigningDatabaseSource(using=None, declared=declared)
    assert (routed.cacheable, unrouted.cacheable) == (declared, declared)


def test_assigning_the_attribute_overrides_the_default_on_any_instance() -> None:
    source = DatabaseSource(using="default")
    source.cacheable = False
    assert source.cacheable is False

    files = FileSystemSource(template_dirs=())
    files.cacheable = False
    assert files.cacheable is False
    assert FileSystemSource(template_dirs=()).cacheable is True


BUILTIN_SOURCES = pytest.mark.parametrize(
    "make",
    [
        lambda: FileSystemSource(template_dirs=()),
        lambda: DatabaseSource(using="default"),
    ],
    ids=["filesystem", "database"],
)


@BUILTIN_SOURCES
def test_deleting_the_assigned_attribute_restores_the_derived_default(make) -> None:
    """The attribute behaves like a plain class attribute that an instance shadows."""
    source = make()
    source.cacheable = False
    assert source.cacheable is False

    del source.cacheable

    assert source.cacheable is True
    assert "cacheable" not in vars(source)


@BUILTIN_SOURCES
def test_patch_object_can_override_and_restore_the_attribute(make) -> None:
    """Test doubles patch the attribute on an instance, as they did on a class one."""
    source = make()

    with patch.object(source, "cacheable", False):
        assert source.cacheable is False

    assert source.cacheable is True
    assert "cacheable" not in vars(source)


def test_filesystem_source_is_still_cached_without_extra_configuration(
    tmp_path: Path,
) -> None:
    (tmp_path / "screen.xml").write_text("<view>first</view>", encoding="utf-8")
    config = _config(FILESYSTEM, "filesystem-cached", TEMPLATE_DIRS=[tmp_path])
    with override_settings(HYPERVIEW=config):
        assert resolve_template("screen.xml").content == "<view>first</view>"
        (tmp_path / "screen.xml").write_text("<view>second</view>", encoding="utf-8")
        assert resolve_template("screen.xml").content == "<view>first</view>"


def test_filesystem_subclass_can_withdraw_the_inherited_declaration(
    tmp_path: Path,
) -> None:
    (tmp_path / "screen.xml").write_text("<view>first</view>", encoding="utf-8")
    config = _config(
        f"{__name__}.TenantFileSource", "filesystem-subclass", TEMPLATE_DIRS=[tmp_path]
    )
    with override_settings(HYPERVIEW=config):
        assert resolve_template("screen.xml").content == "<view>first</view>"
        (tmp_path / "screen.xml").write_text("<view>second</view>", encoding="utf-8")
        assert resolve_template("screen.xml").content == "<view>second</view>"


@pytest.mark.parametrize(
    ("backend", "second_read"),
    [("OverridingFileSource", "second"), ("OptedInFileSource", "first")],
)
def test_filesystem_subclass_that_overrides_resolve_is_cached_only_after_it_opts_in(
    tmp_path: Path, backend: str, second_read: str
) -> None:
    (tmp_path / "screen.xml").write_text("<view>first</view>", encoding="utf-8")
    config = _config(
        f"{__name__}.{backend}", f"filesystem-{backend}", TEMPLATE_DIRS=[tmp_path]
    )
    with override_settings(HYPERVIEW=config):
        assert resolve_template("screen.xml").content == "<view>first</view>"
        (tmp_path / "screen.xml").write_text("<view>second</view>", encoding="utf-8")
        assert resolve_template("screen.xml").content == f"<view>{second_read}</view>"


def test_source_protocol_still_requires_only_resolve() -> None:
    """The documented source protocol is unchanged; cacheable stays optional."""

    class ResolveOnly:
        def resolve(self, name: str) -> None:
            return None

    assert isinstance(ResolveOnly(), TemplateSource)


def test_documentation_states_the_opt_in_contract() -> None:
    """Source authors learn the default, the rule for opting in and the upgrade."""
    guide = " ".join((ROOT / "docs/cache-consistency.md").read_text().split())
    reference = " ".join((ROOT / "docs/api-reference.md").read_text().split())

    assert "Caching is an explicit opt-in per source" in guide
    assert "exactly `True`" in guide
    assert "depends solely on that name and the constructor options" in guide
    assert "ambient state" in guide
    assert "is not cached until it declares `cacheable = True`" in guide
    assert "only when `cacheable` is exactly `True`" in reference
    assert "cache-consistency.md#choose-which-sources-are-cached" in reference
