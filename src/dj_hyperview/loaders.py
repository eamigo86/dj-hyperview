"""Django loader backed by the dj-hyperview resolver."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.template import Origin
from django.template.loaders.base import Loader

from .conf import ValidationSettings
from .exceptions import TemplateNotFound
from .resolver import TemplateResolver
from .sources import ResolvedTemplate
from .validation import validate_template_source

_active_snapshot: ContextVar[dict[str, ResolvedTemplate | None] | None] = ContextVar(
    "dj_hyperview_template_snapshot", default=None
)


@contextmanager
def template_snapshot() -> Iterator[None]:
    """Isolate resolved revisions for one render context."""
    token = _active_snapshot.set({})
    try:
        yield
    finally:
        _active_snapshot.reset(token)


class ResolverOrigin(Origin):
    """A Django origin carrying resolved source content."""

    def __init__(self, resolved: ResolvedTemplate, loader: "ResolverLoader") -> None:
        super().__init__(resolved.origin, resolved.name, loader)
        self.resolved = resolved


class ResolverLoader(Loader):
    """Load templates through an ordered TemplateResolver."""

    def __init__(
        self, engine, resolver: TemplateResolver, validation: ValidationSettings
    ) -> None:
        super().__init__(engine)
        self.resolver = resolver
        self.validation = validation

    def _resolve(self, name: str) -> ResolvedTemplate:
        snapshot = _active_snapshot.get()
        if snapshot is not None and name in snapshot:
            resolved = snapshot[name]
            if resolved is None:
                raise TemplateNotFound(name)
            return resolved
        try:
            resolved = self.resolver.resolve(name)
        except TemplateNotFound:
            if snapshot is not None:
                _active_snapshot.set({**snapshot, name: None})
            raise
        if snapshot is not None:
            _active_snapshot.set({**snapshot, name: resolved})
        return resolved

    def get_template_sources(self, template_name):
        try:
            resolved = self._resolve(template_name)
        except TemplateNotFound:
            return
        yield ResolverOrigin(resolved, self)

    def get_contents(self, origin):
        return validate_template_source(origin.resolved.content, config=self.validation)
