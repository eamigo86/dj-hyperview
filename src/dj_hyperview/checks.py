from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error, register
from django.utils.module_loading import import_string

SETTING = "settings.HYPERVIEW"
DATABASE_SOURCE = "dj_hyperview.contrib.database.sources.DatabaseSource"


def _error(code: str, path: str, requirement: str) -> Error:
    return Error(
        f"{path} {requirement}.",
        hint=f"Correct {path}: it {requirement}.",
        obj=SETTING,
        id=f"dj_hyperview.{code}",
    )


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _check_template_dirs(value: Any) -> list[CheckMessage]:
    if not _sequence(value):
        return [_error("E002", "TEMPLATE_DIRS", "must be a sequence")]
    errors = []
    for directory in value:
        try:
            valid = Path(directory).is_dir()
        except (OSError, TypeError):
            valid = False
        if not valid:
            errors.append(_error("E002", repr(directory), "must be a directory"))
    return errors


def _importable(path: Any) -> bool:
    if not isinstance(path, str) or "." not in path:
        return False
    try:
        return callable(import_string(path))
    except Exception:
        return False


def _check_sources(value: Any) -> list[CheckMessage]:
    if not _sequence(value):
        return [_error("E003", "SOURCES", "must be a sequence")]
    errors = []
    for index, source in enumerate(value):
        path = f"SOURCES[{index}]"
        if not isinstance(source, Mapping):
            errors.append(_error("E003", path, "must be a mapping"))
            continue
        backend = source.get("BACKEND")
        if backend == DATABASE_SOURCE and not apps.is_installed(
            "dj_hyperview.contrib.database"
        ):
            errors.append(_error("E010", backend, "requires its contrib app"))
        elif not _importable(backend):
            errors.append(_error("E003", f"{path}.BACKEND", "must be importable"))
        if not isinstance(source.get("OPTIONS", {}), Mapping):
            errors.append(_error("E003", f"{path}.OPTIONS", "must be a mapping"))
    return errors


def _check_cache(value: Any) -> list[CheckMessage]:
    if not isinstance(value, Mapping):
        return [_error("E004", "CACHE", "must be a mapping")]
    errors = []
    alias = value.get("ALIAS", "default")
    if not isinstance(alias, str) or alias not in settings.CACHES:
        errors.append(_error("E004", "CACHE.ALIAS", "must name a configured cache"))
    for name, default, minimum in (("TTL", 300, 1), ("NEGATIVE_TTL", 15, 0)):
        current = value.get(name, default)
        if (
            isinstance(current, bool)
            or not isinstance(current, int)
            or current < minimum
        ):
            errors.append(
                _error("E005", f"CACHE.{name}", f"must be an integer >= {minimum}")
            )
    if value.get("FAILURE_MODE", "bypass") not in {"bypass", "raise"}:
        errors.append(_error("E006", "CACHE.FAILURE_MODE", "is invalid"))
    return errors


def _schema(value: Any) -> bool:
    if value is None or callable(value):
        return True
    try:
        return Path(value).is_file() or _importable(value)
    except (OSError, TypeError):
        return False


def _check_validation(value: Any) -> list[CheckMessage]:
    if not isinstance(value, Mapping):
        return [_error("E007", "VALIDATION", "must be a mapping")]
    errors = []
    modes = {"publish", "render", "publish_and_render"}
    if value.get("MODE", "publish_and_render") not in modes:
        errors.append(_error("E007", "VALIDATION.MODE", "has an unsupported value"))
    if not _schema(value.get("SCHEMA")):
        errors.append(_error("E008", "VALIDATION.SCHEMA", "must be a path or callable"))
    limits = (("MAX_BYTES", 1_000_000), ("MAX_DEPTH", 64), ("MAX_NODES", 20_000))
    for name, default in limits:
        current = value.get(name, default)
        if isinstance(current, bool) or not isinstance(current, int) or current < 1:
            errors.append(
                _error("E009", f"VALIDATION.{name}", "must be a positive integer")
            )
    return errors


@register("dj_hyperview")
def check_hyperview_settings(
    app_configs: Any = None, **kwargs: Any
) -> list[CheckMessage]:
    del app_configs, kwargs
    raw = getattr(settings, "HYPERVIEW", {})
    if not isinstance(raw, Mapping):
        return [_error("E001", "HYPERVIEW", "must be a mapping")]
    return [
        *_check_template_dirs(raw.get("TEMPLATE_DIRS", ())),
        *_check_sources(raw.get("SOURCES", ())),
        *_check_cache(raw.get("CACHE", {})),
        *_check_validation(raw.get("VALIDATION", {})),
    ]
