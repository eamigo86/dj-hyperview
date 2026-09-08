"""Typed package settings loaded from Django configuration."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from django.conf import settings as django_settings
from django.core.checks import ERROR
from django.dispatch import receiver
from django.http import HttpRequest
from django.test.signals import setting_changed
from django.utils.module_loading import import_string

Schema = str | Path | Callable[[str], None] | None
AdminPermission = Callable[[HttpRequest], bool]
PreviewContext = (
    Mapping[str, Any] | Callable[[HttpRequest | None, str], Mapping[str, Any]]
)
SchemaProfile = Literal["upstream-0.110.0", "compatible-0.110.0"]
SCHEMA_PROFILES: tuple[SchemaProfile, ...] = (
    "upstream-0.110.0",
    "compatible-0.110.0",
)
_SETTING_DEPENDENCIES = frozenset(
    {"CACHES", "DATABASES", "HYPERVIEW", "INSTALLED_APPS"}
)


def _superuser_admin_permission(request: HttpRequest) -> bool:
    """Restrict stored-template mutations to Django superusers by default.

    Args:
        request: Current authenticated admin request.

    Returns:
        Whether the current user is a superuser.
    """
    return bool(request.user.is_superuser)


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
class PreviewScenario:
    """Trusted example context and optional root used by one preview scenario."""

    label: str
    context: PreviewContext
    root_template: str | None = None


def _empty_preview_scenarios() -> Mapping[str, PreviewScenario]:
    """Create the immutable fallback when no example scenarios are configured.

    Returns:
        A single empty scenario with an immutable context mapping.
    """
    return MappingProxyType(
        {"empty": PreviewScenario("No example data", MappingProxyType({}))}
    )


@dataclass(frozen=True, slots=True)
class AdminPreviewSettings:
    """Opt-in preview settings with ordered, server-configured scenarios."""

    enabled: bool = False
    scenarios: Mapping[str, PreviewScenario] = field(
        default_factory=_empty_preview_scenarios
    )


@dataclass(frozen=True, slots=True)
class AdminSettings:
    """Configuration for optional Django Admin enhancements."""

    editor: bool = False
    permission: AdminPermission = _superuser_admin_permission
    preview: AdminPreviewSettings = field(default_factory=AdminPreviewSettings)


@dataclass(frozen=True, slots=True)
class HyperviewSettings:
    """Complete normalized package configuration."""

    template_dirs: tuple[Path, ...] = ()
    sources: tuple[SourceSettings, ...] = ()
    cache: CacheSettings = field(default_factory=CacheSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    admin: AdminSettings = field(default_factory=AdminSettings)
    extra_schemas: tuple[Path, ...] = ()
    schema_profile: SchemaProfile = "upstream-0.110.0"


DEFAULTS = HyperviewSettings()


def _section(raw: Mapping[str, Any], defaults: Any) -> dict[str, Any]:
    return {
        item.name: raw.get(item.name.upper(), getattr(defaults, item.name))
        for item in fields(defaults)
    }


def _admin_settings(raw: Mapping[str, Any]) -> AdminSettings:
    """Normalize editor, mutation permission, and preview configuration.

    Args:
        raw: Raw ADMIN mapping from Django settings.

    Returns:
        Immutable normalized admin configuration.
    """
    values = _section(raw, DEFAULTS.admin)
    permission = values["permission"]
    if isinstance(permission, str):
        permission = import_string(permission)
    preview = raw.get("PREVIEW", {})
    scenarios = {}
    for key, scenario in preview.get("SCENARIOS", {}).items():
        context = scenario["CONTEXT"]
        if isinstance(context, str):
            context = import_string(context)
        if isinstance(context, Mapping):
            context = MappingProxyType(dict(context))
        scenarios[key] = PreviewScenario(
            scenario["LABEL"], context, scenario.get("ROOT_TEMPLATE")
        )
    return AdminSettings(
        editor=values["editor"],
        permission=permission,
        preview=AdminPreviewSettings(
            enabled=preview.get("ENABLED", False),
            scenarios=MappingProxyType(scenarios)
            if scenarios
            else _empty_preview_scenarios(),
        ),
    )


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
        admin=_admin_settings(raw.get("ADMIN", {})),
        extra_schemas=tuple(Path(path) for path in raw.get("EXTRA_SCHEMAS", ())),
        schema_profile=raw.get("SCHEMA_PROFILE", DEFAULTS.schema_profile),
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
