"""Ordered resolution across configured template sources."""

from collections.abc import Iterable

from django.utils.module_loading import import_string

from .conf import get_settings
from .exceptions import TemplateNotFound
from .sources import ResolvedTemplate, TemplateSource, canonicalize_template_name


class TemplateResolver:
    """Return the first template found by an ordered source collection."""

    def __init__(self, sources: Iterable[TemplateSource]) -> None:
        self.sources = tuple(sources)

    @classmethod
    def from_settings(cls) -> "TemplateResolver":
        configured = get_settings().sources
        return cls(
            import_string(source.backend)(**source.options) for source in configured
        )

    def resolve(self, name: str) -> ResolvedTemplate:
        canonical = canonicalize_template_name(name)
        for source in self.sources:
            resolved = source.resolve(canonical)
            if resolved is not None:
                return resolved
        raise TemplateNotFound(canonical)


def resolve_template(name: str) -> ResolvedTemplate:
    """Resolve a template using the current Django settings."""
    return TemplateResolver.from_settings().resolve(name)
