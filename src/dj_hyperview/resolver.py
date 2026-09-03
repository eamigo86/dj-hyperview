"""Ordered resolution across configured template sources."""

import hashlib
import json
import math
from collections.abc import Iterable
from pathlib import PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath

from django.conf import settings as django_settings
from django.utils.module_loading import import_string

from .cache import CACHE_MISS, TemplateCache
from .conf import get_settings
from .exceptions import SourceUnavailable, TemplateNotFound
from .sources import (
    FileSystemSource,
    ResolvedTemplate,
    TemplateSource,
    canonicalize_template_name,
)

_PATH_TYPES = (PurePosixPath, PureWindowsPath, PosixPath, WindowsPath)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        errors="surrogatepass"
    )


def _normalize_path(value: PurePath) -> object:
    dialect = "windows" if isinstance(value, PureWindowsPath) else "posix"
    return ["path", dialect, str(value)]


def _normalize_mapping_key(value: object) -> object:
    value_type = type(value)
    if value_type is bool:
        return ["number", int(value), 1]
    if value_type is int:
        return ["number", value, 1]
    if value_type is float:
        if not math.isfinite(value):
            raise TypeError
        numerator, denominator = value.as_integer_ratio()
        return ["number", numerator, denominator]
    if value_type in _PATH_TYPES:
        return _normalize_path(value)
    if value_type is tuple:
        return ["tuple", [_normalize_mapping_key(item) for item in value]]
    return _normalize_fingerprint(value)


def _normalize_fingerprint(value: object) -> object:
    value_type = type(value)
    if value is None:
        return ["none"]
    if value_type is bool:
        return ["bool", value]
    if value_type is int:
        return ["int", value]
    if value_type is float:
        if not math.isfinite(value):
            raise TypeError
        return ["float", value]
    if value_type in _PATH_TYPES:
        return _normalize_path(value)
    if value_type is str:
        return ["str", value]
    if value_type is bytes:
        return ["bytes", value.hex()]
    if callable(value):
        module = getattr(value, "__module__", None)
        qualname = getattr(value, "__qualname__", None)
        if not module or not qualname or "<locals>" in qualname:
            raise TypeError
        path = f"{module}.{qualname}"
        if import_string(path) is not value:
            raise TypeError
        return ["callable", path]
    if value_type is dict:
        pairs = [
            (_normalize_mapping_key(key), _normalize_fingerprint(item))
            for key, item in value.items()
        ]
        pairs.sort(key=_canonical_bytes)
        return ["mapping", pairs]
    if value_type in (list, tuple):
        kind = f"{value_type.__module__}.{value_type.__qualname__}"
        return ["sequence", kind, [_normalize_fingerprint(item) for item in value]]
    raise TypeError


def _source_fingerprint(index: int, backend: str, options: object) -> str | None:
    """Return a secret-safe stable identity, or disable caching when impossible."""
    try:
        normalized = _normalize_fingerprint([index, backend, options])
        digest = hashlib.sha256(_canonical_bytes(normalized)).hexdigest()
    except Exception:  # Arbitrary consumer configuration is not a trust boundary.
        return None
    return f"source:{digest}"


class TemplateResolver:
    """Return the first template found by an ordered source collection."""

    def __init__(
        self,
        sources: Iterable[TemplateSource],
        *,
        cache: TemplateCache | None = None,
        failure_mode: str = "bypass",
        _source_ids: Iterable[str | None] | None = None,
    ) -> None:
        self.sources = tuple(sources)
        if failure_mode not in {"bypass", "raise"}:
            raise ValueError("Cache failure mode must be 'bypass' or 'raise'")
        self.cache = cache
        self.failure_mode = failure_mode
        self._source_ids = tuple(_source_ids or self._default_source_ids())
        if len(self._source_ids) != len(self.sources):
            raise ValueError("Each template source requires one cache identity")

    def _default_source_ids(self) -> Iterable[str | None]:
        for index, source in enumerate(self.sources):
            kind = type(source)
            try:
                options = vars(source)
            except TypeError:
                options = source
            yield _source_fingerprint(
                index, f"{kind.__module__}.{kind.__qualname__}", options
            )

    @classmethod
    def from_settings(cls) -> "TemplateResolver":
        config = get_settings()
        sources = tuple(
            import_string(source.backend)(**source.options) for source in config.sources
        )
        raw = getattr(django_settings, "HYPERVIEW", {})
        if not raw.get("CACHE"):
            return cls(sources)
        try:
            cache = TemplateCache.from_settings(config.cache.namespace)
        except SourceUnavailable:
            if config.cache.failure_mode == "raise":
                raise
            return cls(sources)
        source_ids = []
        for index, (source, instance) in enumerate(
            zip(config.sources, sources, strict=True)
        ):
            options = dict(source.options)
            if isinstance(instance, FileSystemSource):
                options["template_dirs"] = instance.template_dirs
            source_ids.append(_source_fingerprint(index, source.backend, options))
        return cls(
            sources,
            cache=cache,
            failure_mode=config.cache.failure_mode,
            _source_ids=source_ids,
        )

    def resolve(self, name: str) -> ResolvedTemplate:
        canonical = canonicalize_template_name(name)
        for source, source_id in zip(self.sources, self._source_ids, strict=True):
            resolved = self._resolve_source(source, source_id, canonical)
            if resolved is not None:
                return resolved
        raise TemplateNotFound(canonical)

    def _resolve_source(
        self, source: TemplateSource, source_id: str | None, name: str
    ) -> ResolvedTemplate | None:
        if self.cache is None or source_id is None:
            return source.resolve(name)
        try:
            entry = self.cache.get_resolved(source_id, name)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise
            return source.resolve(name)
        if entry is CACHE_MISS:
            return None
        if entry is not None:
            return entry.template

        resolved = source.resolve(name)
        try:
            if resolved is None:
                self.cache.set_resolved_miss(source_id, name)
            else:
                self.cache.set_resolved(source_id, name, resolved)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise
        return resolved


def resolve_template(name: str) -> ResolvedTemplate:
    """Resolve a template using the current Django settings."""
    return TemplateResolver.from_settings().resolve(name)
