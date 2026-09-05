"""Ordered resolution across configured template sources."""

import hashlib
import json
import math
from collections.abc import Iterable
from inspect import getattr_static
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
_MISSING_CACHE_MARKER = object()
_MISSING_CACHE_SAFETY_HOOK = object()


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


def _source_cacheable(source: object) -> bool:
    try:
        static_marker = getattr_static(
            source, "_dj_hyperview_cacheable", _MISSING_CACHE_MARKER
        )
    except Exception:
        return False
    try:
        value = getattr(source, "_dj_hyperview_cacheable", _MISSING_CACHE_MARKER)
    except Exception:
        return False
    if value is _MISSING_CACHE_MARKER:
        return static_marker is _MISSING_CACHE_MARKER
    return type(value) is bool and value


def _source_cache_safe(source: object) -> bool:
    try:
        static_hook = getattr_static(
            source, "_dj_hyperview_cache_safe", _MISSING_CACHE_SAFETY_HOOK
        )
    except Exception:
        return False
    if static_hook is _MISSING_CACHE_SAFETY_HOOK:
        return True
    try:
        hook = source._dj_hyperview_cache_safe  # type: ignore[attr-defined]
        value = hook()
    except Exception:
        return False
    return type(value) is bool and value


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
        """Initialize an ordered template resolver.

        Args:
            sources: Ordered raw-template sources.
            cache: Optional raw-template cache.
            failure_mode: Cache failure policy, either bypass or raise.
            _source_ids: Precomputed source identities for internal construction.

        Raises:
            ValueError: If the cache policy or source identity count is invalid.
        """
        self.sources = tuple(sources)
        if failure_mode not in {"bypass", "raise"}:
            raise ValueError("Cache failure mode must be 'bypass' or 'raise'")
        self.cache = cache
        self.failure_mode = failure_mode
        identities = self._default_source_ids() if _source_ids is None else _source_ids
        self._source_ids = tuple(identities)
        if len(self._source_ids) != len(self.sources):
            raise ValueError("Each template source requires one cache identity")

    def _default_source_ids(self) -> Iterable[str | None]:
        for index, source in enumerate(self.sources):
            if not _source_cacheable(source):
                yield None
                continue
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
        """Create a resolver from validated current Django settings.

        Returns:
            A resolver with ordered sources and optional raw caching.

        Raises:
            HyperviewConfigurationError: If Hyperview settings are invalid.
            SourceUnavailable: If cache initialization fails in raise mode.
            Exception: If cache initialization reraises an unclassified error.
        """
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
            if not _source_cacheable(instance):
                source_ids.append(None)
                continue
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
        """Resolve one template according to configured source precedence.

        Args:
            name: Canonicalizable consumer template name.

        Returns:
            The first matching raw template.

        Raises:
            InvalidTemplateName: If the name is unsafe or non-canonical.
            SourceUnavailable: If a source or required cache operation fails.
            TemplateNotFound: If every configured source misses.
        """
        canonical = canonicalize_template_name(name)
        generation, initialize = self._generation(canonical)
        initial_results: list[tuple[str, ResolvedTemplate | None]] = []
        for source, source_id in zip(self.sources, self._source_ids, strict=True):
            publish_initial = False
            if initialize:
                publish_initial = source_id is not None and _source_cache_safe(source)
                resolved = source.resolve(canonical)
                if publish_initial:
                    initial_results.append((source_id, resolved))
            else:
                resolved = self._resolve_source(
                    source, source_id, canonical, generation
                )
            if resolved is not None:
                if initialize and publish_initial:
                    self._publish_initial(canonical, initial_results)
                return resolved
        raise TemplateNotFound(canonical)

    def _generation(self, name: str) -> tuple[str | None, bool]:
        if self.cache is None or not any(
            source_id is not None for source_id in self._source_ids
        ):
            return None, False
        try:
            generation = self.cache._peek_generation(name)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise
            return None, False
        return generation, generation is None

    def _publish_initial(
        self,
        name: str,
        results: list[tuple[str, ResolvedTemplate | None]],
    ) -> None:
        try:
            generation, created = self.cache._initialize_generation(name)
            if created:
                for source_id, resolved in results:
                    if resolved is None:
                        self.cache.set_resolved_miss(source_id, name, generation)
                    else:
                        self.cache.set_resolved(source_id, name, resolved, generation)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise

    def _resolve_source(
        self,
        source: TemplateSource,
        source_id: str | None,
        name: str,
        generation: str | None,
    ) -> ResolvedTemplate | None:
        if self.cache is None or source_id is None or generation is None:
            return source.resolve(name)
        try:
            entry = self.cache.get_resolved(source_id, name, generation)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise
            return source.resolve(name)
        if entry is CACHE_MISS:
            return None
        if entry is not None:
            return entry.template

        resolved = source.resolve(name)
        if not _source_cache_safe(source):
            return resolved
        try:
            if resolved is None:
                self.cache.set_resolved_miss(source_id, name, generation)
            else:
                self.cache.set_resolved(source_id, name, resolved, generation)
        except SourceUnavailable:
            if self.failure_mode == "raise":
                raise
        return resolved


def resolve_template(name: str) -> ResolvedTemplate:
    """Resolve a template using the current Django settings.

    Args:
        name: Canonicalizable consumer template name.

    Returns:
        The first matching raw template.

    Raises:
        InvalidTemplateName: If the name is unsafe or non-canonical.
        SourceUnavailable: If a source or required cache operation fails.
        TemplateNotFound: If every configured source misses.
    """
    return TemplateResolver.from_settings().resolve(name)
