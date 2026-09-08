"""Django system checks for consumer Hyperview configuration."""

from collections.abc import Mapping, Sequence
from importlib.util import find_spec
from inspect import signature
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.cache.backends.dummy import DummyCache
from django.core.checks import CheckMessage, Error, Warning, register
from django.utils.module_loading import import_string

from .cache import _BACKEND_FAILURE, _UNSUPPORTED_BACKEND, _resolve_cache_alias
from .conf import SCHEMA_PROFILES
from .sources import FileSystemSource

SETTING = "settings.HYPERVIEW"
DATABASE_SOURCE = "dj_hyperview.contrib.database.sources.DatabaseSource"
FILESYSTEM_SOURCE = "dj_hyperview.sources.FileSystemSource"


def _error(code: str, path: str, requirement: str) -> Error:
    return Error(
        f"{path} {requirement}.",
        hint=f"Correct {path}: it {requirement}.",
        obj=SETTING,
        id=f"dj_hyperview.{code}",
    )


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _check_template_dirs(value: Any, path: str = "TEMPLATE_DIRS") -> list[CheckMessage]:
    if not _sequence(value):
        return [_error("E002", path, "must be a sequence")]
    errors = []
    for index, directory in enumerate(value):
        item_path = f"{path}[{index}]"
        try:
            directory_path = Path(directory)
        except TypeError:
            errors.append(_error("E002", item_path, "must be a path"))
            continue
        try:
            valid = directory_path.is_dir()
        except OSError:
            valid = False
        if not valid:
            errors.append(
                Warning(
                    f"{item_path} is not currently an available directory.",
                    hint=(
                        f"Create or mount {item_path}; unavailable roots are skipped "
                        "at runtime."
                    ),
                    obj=SETTING,
                    id="dj_hyperview.W006",
                )
            )
    return errors


def _importable(path: Any) -> bool:
    if not isinstance(path, str) or "." not in path:
        return False
    try:
        return callable(import_string(path))
    except Exception:
        return False


def _is_filesystem_backend(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    try:
        backend = import_string(path)
    except Exception:
        return False
    return isinstance(backend, type) and issubclass(backend, FileSystemSource)


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
        options = source.get("OPTIONS", {})
        if (
            _is_filesystem_backend(backend)
            and isinstance(options, Mapping)
            and "template_dirs" in options
        ):
            errors.extend(
                _check_template_dirs(
                    options["template_dirs"], f"{path}.OPTIONS.template_dirs"
                )
            )
        if backend == DATABASE_SOURCE:
            if not apps.is_installed("dj_hyperview.contrib.database"):
                errors.append(_error("E010", backend, "requires its contrib app"))
            if isinstance(options, Mapping):
                from .contrib.database._config import _database_alias_is_configured

                if not _database_alias_is_configured(options.get("using")):
                    errors.append(
                        _error(
                            "E011",
                            f"{path}.OPTIONS.using",
                            "must be None or name a configured database",
                        )
                    )
        elif not _importable(backend):
            errors.append(_error("E003", f"{path}.BACKEND", "must be importable"))
        if not isinstance(options, Mapping):
            errors.append(_error("E003", f"{path}.OPTIONS", "must be a mapping"))
    return errors


def _source_consistency_warnings(
    raw: Mapping[str, Any], template_dir_messages: Sequence[CheckMessage]
) -> list[CheckMessage]:
    template_dirs = raw.get("TEMPLATE_DIRS", ())
    sources = raw.get("SOURCES", ())
    if not _sequence(sources):
        return []

    filesystem_sources = [
        source
        for source in sources
        if isinstance(source, Mapping) and _is_filesystem_backend(source.get("BACKEND"))
    ]
    warnings = []
    valid_template_dirs = (
        _sequence(template_dirs)
        and bool(template_dirs)
        and not any(isinstance(message, Error) for message in template_dir_messages)
    )
    if valid_template_dirs and not filesystem_sources:
        warnings.append(
            Warning(
                "TEMPLATE_DIRS is configured but no FileSystemSource consumes it.",
                hint="Add the built-in FileSystemSource or remove TEMPLATE_DIRS.",
                obj=SETTING,
                id="dj_hyperview.W002",
            )
        )

    source_without_roots = False
    for source in filesystem_sources:
        options = source.get("OPTIONS", {})
        if not isinstance(options, Mapping):
            continue
        roots = options.get("template_dirs", template_dirs)
        if _sequence(roots) and not roots:
            source_without_roots = True
    if source_without_roots:
        warnings.append(
            Warning(
                "FileSystemSource is configured without any template roots.",
                hint=(
                    "Set TEMPLATE_DIRS or provide OPTIONS.template_dirs for the source."
                ),
                obj=SETTING,
                id="dj_hyperview.W003",
            )
        )
    if not sources and (not raw or "SOURCES" in raw):
        warnings.append(
            Warning(
                "No SOURCES configured; HyperviewTemplateResponse resolves no "
                "templates.",
                hint="Add a source or pass using='django'.",
                obj=SETTING,
                id="dj_hyperview.W005",
            )
        )
    return warnings


def _check_cache(value: Any) -> list[CheckMessage]:
    if not isinstance(value, Mapping):
        return [_error("E004", "CACHE", "must be a mapping")]
    errors = []
    alias = value.get("ALIAS", "default")
    backend, alias_error = _resolve_cache_alias(alias)
    if alias_error == _BACKEND_FAILURE:
        errors.append(
            Warning(
                "CACHE.ALIAS references an unavailable backend.",
                hint="Cache failure behavior follows CACHE.FAILURE_MODE at runtime.",
                obj=SETTING,
                id="dj_hyperview.W001",
            )
        )
    elif alias_error == _UNSUPPORTED_BACKEND:
        errors.append(
            _error(
                "E018",
                "CACHE.ALIAS",
                "must use a backend with atomic add; FileBasedCache is unsupported",
            )
        )
    elif alias_error is not None:
        errors.append(_error("E004", "CACHE.ALIAS", "must name a configured cache"))
    elif isinstance(backend, DummyCache):
        errors.append(
            Warning(
                "CACHE.ALIAS references DummyCache, which cannot store templates.",
                hint=(
                    "Remove CACHE to disable Hyperview caching or configure a "
                    "stateful backend."
                ),
                obj=SETTING,
                id="dj_hyperview.W004",
            )
        )
    namespace = value.get("NAMESPACE", "dj-hyperview")
    if not isinstance(namespace, str) or not namespace:
        errors.append(_error("E004", "CACHE.NAMESPACE", "must be a non-empty string"))
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
        is_file = Path(value).is_file()
    except (OSError, TypeError):
        return False
    if not is_file:
        return _importable(value)
    from .exceptions import TemplateValidationError
    from .validation import _compile_schema

    try:
        _compile_schema(value)
    except TemplateValidationError:
        return False
    return True


def _check_validation(value: Any) -> list[CheckMessage]:
    if not isinstance(value, Mapping):
        return [_error("E007", "VALIDATION", "must be a mapping")]
    errors = []
    modes = {"publish", "render", "publish_and_render"}
    if value.get("MODE", "publish_and_render") not in modes:
        errors.append(_error("E007", "VALIDATION.MODE", "has an unsupported value"))
    schema = value.get("SCHEMA")
    if not _schema(schema):
        errors.append(_error("E008", "VALIDATION.SCHEMA", "must be a path or callable"))
    elif (
        schema == "dj_hyperview.validate_hyperview_schema"
        and find_spec("xmlschema") is None
    ):
        errors.append(
            Error(
                "VALIDATION.SCHEMA requires the optional schema dependency.",
                hint="Install dj-hyperview[schema] or set VALIDATION.SCHEMA to None.",
                obj=SETTING,
                id="dj_hyperview.E013",
            )
        )
    limits = (("MAX_BYTES", 1_000_000), ("MAX_DEPTH", 64), ("MAX_NODES", 20_000))
    for name, default in limits:
        current = value.get(name, default)
        if isinstance(current, bool) or not isinstance(current, int) or current < 1:
            errors.append(
                _error("E009", f"VALIDATION.{name}", "must be a positive integer")
            )
        elif name == "MAX_DEPTH" and current > 256:
            errors.append(
                _error("E009", "VALIDATION.MAX_DEPTH", "must be no greater than 256")
            )
    return errors


def _check_extra_schemas(value: Any) -> list[CheckMessage]:
    if not _sequence(value):
        return [_error("E012", "EXTRA_SCHEMAS", "must be a sequence of local files")]
    from .exceptions import TemplateValidationError
    from .schema import _compile_schema, _guard_local_references

    errors = []
    for index, configured in enumerate(value):
        path = f"EXTRA_SCHEMAS[{index}]"
        try:
            schema_path = Path(configured)
            if not schema_path.is_file():
                raise OSError
            _guard_local_references(schema_path)
            _compile_schema(schema_path)
        except (OSError, TypeError, TemplateValidationError):
            errors.append(_error("E012", path, "must be a valid local XSD 1.1 schema"))
    return errors


def _check_schema_profile(value: Any) -> list[CheckMessage]:
    if not isinstance(value, str) or value not in SCHEMA_PROFILES:
        return [
            _error(
                "E019",
                "SCHEMA_PROFILE",
                "must be upstream-0.110.0 or compatible-0.110.0",
            )
        ]
    return []


def _check_admin(value: Any) -> list[CheckMessage]:
    if not isinstance(value, Mapping):
        return [_error("E014", "ADMIN", "must be a mapping")]
    errors = []
    editor = value.get("EDITOR", False)
    if not isinstance(editor, bool):
        errors.append(_error("E014", "ADMIN.EDITOR", "must be a boolean"))
    elif editor:
        if find_spec("django_ace") is None:
            errors.append(
                Error(
                    "ADMIN.EDITOR requires the optional editor dependency.",
                    hint="Install dj-hyperview[editor] before enabling ADMIN.EDITOR.",
                    obj=SETTING,
                    id="dj_hyperview.E015",
                )
            )
        elif not apps.is_installed("django_ace"):
            errors.append(
                Error(
                    "ADMIN.EDITOR requires django_ace in INSTALLED_APPS.",
                    hint="Add django_ace before dj_hyperview in INSTALLED_APPS.",
                    obj=SETTING,
                    id="dj_hyperview.E016",
                )
            )
        elif find_spec("xmlschema") is None:
            errors.append(
                Error(
                    "ADMIN.EDITOR static XSD validation requires xmlschema.",
                    hint="Install dj-hyperview[editor] before enabling ADMIN.EDITOR.",
                    obj=SETTING,
                    id="dj_hyperview.E020",
                )
            )

    permission = value.get("PERMISSION", lambda request: True)
    if isinstance(permission, str):
        try:
            permission = import_string(permission)
        except Exception:
            permission = None
    accepts_request = callable(permission)
    if accepts_request:
        try:
            signature(permission).bind(object())
        except (TypeError, ValueError):
            accepts_request = False
    if not accepts_request:
        errors.append(
            _error(
                "E017",
                "ADMIN.PERMISSION",
                "must be a callable accepting request or a dotted path to one",
            )
        )
    return errors


@register("dj_hyperview")
def check_hyperview_settings(
    app_configs: Any = None, **kwargs: Any
) -> list[CheckMessage]:
    """Validate the package configuration through Django's checks framework.

    Args:
        app_configs: Optional application subset supplied by Django.
        **kwargs: Additional check-runner options supplied by Django.

    Returns:
        Actionable errors and operational warnings for current settings.
    """
    del app_configs, kwargs
    raw = getattr(settings, "HYPERVIEW", {})
    if not isinstance(raw, Mapping):
        return [_error("E001", "HYPERVIEW", "must be a mapping")]
    cache = raw.get("CACHE")
    cache_errors = (
        []
        if "CACHE" not in raw or (isinstance(cache, Mapping) and not cache)
        else _check_cache(cache)
    )
    template_dir_messages = _check_template_dirs(raw.get("TEMPLATE_DIRS", ()))
    return [
        *template_dir_messages,
        *_check_sources(raw.get("SOURCES", ())),
        *_source_consistency_warnings(raw, template_dir_messages),
        *cache_errors,
        *_check_validation(raw.get("VALIDATION", {})),
        *_check_extra_schemas(raw.get("EXTRA_SCHEMAS", ())),
        *_check_schema_profile(raw.get("SCHEMA_PROFILE", "upstream-0.110.0")),
        *_check_admin(raw.get("ADMIN", {})),
    ]
