"""Django loader backed by the dj-hyperview resolver."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.template import Engine, Origin
from django.template.loaders.base import Loader

from .conf import ValidationSettings
from .exceptions import TemplateNotFound
from .resolver import TemplateResolver
from .sources import ResolvedTemplate
from .validation import validate_template_source


@dataclass(frozen=True, slots=True)
class _ResolverSnapshot:
    resolver: TemplateResolver
    templates: dict[str, ResolvedTemplate | None]


_active_snapshots: ContextVar[tuple[_ResolverSnapshot, ...]] = ContextVar(
    "dj_hyperview_template_snapshots", default=()
)


def _snapshot_for(
    resolver: TemplateResolver,
) -> dict[str, ResolvedTemplate | None] | None:
    for snapshot in _active_snapshots.get():
        if snapshot.resolver is resolver:
            return snapshot.templates
    return None


def _remember(
    resolver: TemplateResolver, name: str, resolved: ResolvedTemplate | None
) -> None:
    _active_snapshots.set(
        tuple(
            _ResolverSnapshot(resolver, {**snapshot.templates, name: resolved})
            if snapshot.resolver is resolver
            else snapshot
            for snapshot in _active_snapshots.get()
        )
    )


@contextmanager
def template_snapshot(resolver: TemplateResolver) -> Iterator[None]:
    """Isolate resolved revisions per resolver within one render context.

    Args:
        resolver: Resolver that owns the snapshot.

    Returns:
        A context manager that isolates one render snapshot.
    """
    if _snapshot_for(resolver) is not None:
        yield
        return
    _active_snapshots.set((*_active_snapshots.get(), _ResolverSnapshot(resolver, {})))
    try:
        yield
    finally:
        _active_snapshots.set(
            tuple(
                snapshot
                for snapshot in _active_snapshots.get()
                if snapshot.resolver is not resolver
            )
        )


class ResolverOrigin(Origin):
    """A Django origin carrying resolved source content."""

    def __init__(self, resolved: ResolvedTemplate, loader: "ResolverLoader") -> None:
        """Initialize an origin from resolved template metadata.

        Args:
            resolved: Resolved raw template metadata.
            loader: Loader that produced the origin.
        """
        super().__init__(resolved.origin, resolved.name, loader)
        self.resolved = resolved


class ResolverLoader(Loader):
    """Load templates through an ordered TemplateResolver."""

    def __init__(
        self,
        engine: Engine,
        resolver: TemplateResolver,
        validation: ValidationSettings,
    ) -> None:
        """Initialize a resolver-backed Django loader.

        Args:
            engine: Owning Django template engine.
            resolver: Ordered raw-template resolver.
            validation: Validation policy for loaded source.
        """
        super().__init__(engine)
        self.resolver = resolver
        self.validation = validation

    def _resolve(self, name: str) -> ResolvedTemplate:
        snapshot = _snapshot_for(self.resolver)
        if snapshot is not None and name in snapshot:
            resolved = snapshot[name]
            if resolved is None:
                raise TemplateNotFound(name)
            return resolved
        try:
            resolved = self.resolver.resolve(name)
        except TemplateNotFound:
            if snapshot is not None:
                _remember(self.resolver, name, None)
            raise
        if snapshot is not None:
            _remember(self.resolver, name, resolved)
        return resolved

    def get_template_sources(self, template_name: str) -> Iterator[ResolverOrigin]:
        """Yield the resolver origin for an available canonical template.

        Args:
            template_name: Canonical template name.

        Returns:
            Available origins for the canonical template.
        """
        try:
            resolved = self._resolve(template_name)
        except TemplateNotFound:
            return
        yield ResolverOrigin(resolved, self)

    def get_contents(self, origin: ResolverOrigin) -> str:
        """Return validated source content from a resolver origin.

        Args:
            origin: Resolver origin containing raw source content.

        Returns:
            The validated source content.
        """
        return validate_template_source(origin.resolved.content, config=self.validation)
