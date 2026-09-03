"""Ordered resolution across configured template sources."""

from collections.abc import Iterable

from django.conf import settings as django_settings
from django.utils.module_loading import import_string

from .cache import CACHE_MISS, TemplateCache
from .conf import get_settings
from .exceptions import SourceUnavailable, TemplateNotFound
from .sources import ResolvedTemplate, TemplateSource, canonicalize_template_name


class TemplateResolver:
    """Return the first template found by an ordered source collection."""

    def __init__(
        self,
        sources: Iterable[TemplateSource],
        *,
        cache: TemplateCache | None = None,
        failure_mode: str = "bypass",
        _source_ids: Iterable[str] | None = None,
    ) -> None:
        self.sources = tuple(sources)
        if failure_mode not in {"bypass", "raise"}:
            raise ValueError("Cache failure mode must be 'bypass' or 'raise'")
        self.cache = cache
        self.failure_mode = failure_mode
        self._source_ids = tuple(_source_ids or self._default_source_ids())
        if len(self._source_ids) != len(self.sources):
            raise ValueError("Each template source requires one cache identity")

    def _default_source_ids(self) -> Iterable[str]:
        for index, source in enumerate(self.sources):
            kind = type(source)
            yield f"{index}:{kind.__module__}.{kind.__qualname__}"

    @classmethod
    def from_settings(cls) -> "TemplateResolver":
        config = get_settings()
        sources = tuple(
            import_string(source.backend)(**source.options) for source in config.sources
        )
        raw = getattr(django_settings, "HYPERVIEW", {})
        if "CACHE" not in raw:
            return cls(sources)
        try:
            cache = TemplateCache.from_settings(config.cache.namespace)
        except SourceUnavailable:
            if config.cache.failure_mode == "raise":
                raise
            return cls(sources)
        source_ids = (
            f"{index}:{source.backend}" for index, source in enumerate(config.sources)
        )
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
        self, source: TemplateSource, source_id: str, name: str
    ) -> ResolvedTemplate | None:
        if self.cache is None:
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
