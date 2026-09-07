"""Typed package settings loaded from Django configuration."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from django.conf import settings as django_settings
from django.core.checks import ERROR
from django.dispatch import receiver
from django.test.signals import setting_changed

Schema = str | Path | Callable[[str], None] | None
_SETTING_DEPENDENCIES = frozenset(
    {"CACHES", "DATABASES", "HYPERVIEW", "INSTALLED_APPS"}
)


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Configuration for one ordered template source."""

    backend: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CacheSettings:
    """Configuration for optional raw-template caching."""

    alias: str = "default"
    ttl: int = 300
    negative_ttl: int = 15
    failure_mode: str = "bypass"
    namespace: str = "dj-hyperview"


@dataclass(frozen=True, slots=True)
class ValidationSettings:
    """Configuration for safe source and rendered HXML validation."""

    mode: str = "publish_and_render"
    schema: Schema = None
    max_bytes: int = 1_000_000
    max_depth: int = 64
    max_nodes: int = 20_000


@dataclass(frozen=True, slots=True)
class AdminSettings:
    """Configuration for optional Django Admin enhancements."""

    editor: bool = False


@dataclass(frozen=True, slots=True)
class HyperviewSettings:
    """Complete normalized package configuration."""

    template_dirs: tuple[Path, ...] = ()
    sources: tuple[SourceSettings, ...] = ()
    cache: CacheSettings = field(default_factory=CacheSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    admin: AdminSettings = field(default_factory=AdminSettings)
    extra_schemas: tuple[Path, ...] = ()


DEFAULTS = HyperviewSettings()


def _section(raw: Mapping[str, Any], defaults: Any) -> dict[str, Any]:
    return {
        item.name: raw.get(item.name.upper(), getattr(defaults, item.name))
        for item in fields(defaults)
    }


@lru_cache(maxsize=1)
def _settings_snapshot() -> HyperviewSettings:
    from .checks import check_hyperview_settings
    from .exceptions import HyperviewConfigurationError

    errors = [error for error in check_hyperview_settings() if error.level >= ERROR]
    if errors:
        raise HyperviewConfigurationError(
            [f"{error.id}: {error.msg}" for error in errors]
        )

    raw = getattr(django_settings, "HYPERVIEW", {})
    return HyperviewSettings(
        template_dirs=tuple(Path(path) for path in raw.get("TEMPLATE_DIRS", ())),
        sources=tuple(
            SourceSettings(
                backend=source["BACKEND"],
                options=MappingProxyType(dict(source.get("OPTIONS", {}))),
            )
            for source in raw.get("SOURCES", ())
        ),
        cache=CacheSettings(**_section(raw.get("CACHE", {}), DEFAULTS.cache)),
        validation=ValidationSettings(
            **_section(raw.get("VALIDATION", {}), DEFAULTS.validation)
        ),
        admin=AdminSettings(**_section(raw.get("ADMIN", {}), DEFAULTS.admin)),
        extra_schemas=tuple(Path(path) for path in raw.get("EXTRA_SCHEMAS", ())),
    )


def get_settings() -> HyperviewSettings:
    """Validate and return current normalized Hyperview settings.

    Returns:
        The normalized package configuration.

    Raises:
        HyperviewConfigurationError: If package settings are invalid.
    """
    return _settings_snapshot()


@receiver(
    setting_changed,
    dispatch_uid="dj_hyperview.clear_settings_snapshot",
    weak=False,
)
def _clear_settings_snapshot(*, setting: str, **kwargs: Any) -> None:
    del kwargs
    if setting in _SETTING_DEPENDENCIES:
        _settings_snapshot.cache_clear()
