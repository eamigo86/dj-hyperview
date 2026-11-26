"""Django loader backed by the dj-hyperview resolver."""

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.template import Engine, Origin, Template, TemplateDoesNotExist
from django.template.loaders.base import Loader

from .conf import ValidationSettings, get_settings
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
# The validation policy, then the source, name and origin of a resolution. The
# revision is not part of it, so a republished template replaces its entry.
_CompiledKey = tuple[ValidationSettings, str, str, str]


class _CompiledCache:
    """The compiled templates of a loader, with their size and the lock around them.

    A deep copy starts empty: the lock cannot be copied and the compiled templates
    belong to the original loader, so the copy compiles its own. The loader needs no
    copy protocol of its own for it.
    """

    __slots__ = ("entries", "size", "lock")

    def __init__(self) -> None:
        self.entries: OrderedDict[_CompiledKey, tuple[ResolvedTemplate, Template]] = (
            OrderedDict()
        )
        self.size = 0
        self.lock = threading.Lock()

    def __deepcopy__(self, memo: dict[int, object]) -> "_CompiledCache":
        return _CompiledCache()


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

    Yields:
        Nothing; the with block runs inside the resolver's snapshot, which an
        enclosing block for the same resolver keeps instead of replacing.
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
    """Load templates through an ordered TemplateResolver.

    The loader keeps the templates it compiled, so a template that resolves to the
    same content is validated and compiled once. The resolver still runs on every
    lookup, and a template is reused only while its whole resolution is unchanged.
    A template keeps one entry per source, name and origin, which a changed
    resolution replaces. The memory is bounded by an entry count and by the total
    number of characters of source, and the least recently used template leaves
    first. The memory a compiled template takes is estimated under "Template engine
    behavior" in the configuration guide.
    """

    _COMPILED_MAX_ENTRIES = 256
    _COMPILED_MAX_CHARS = 512 * 1024

    def __init__(
        self,
        engine: Engine,
        resolver: TemplateResolver | None = None,
        validation: ValidationSettings | None = None,
    ) -> None:
        """Initialize a resolver-backed Django loader.

        Django builds a loader named in TEMPLATES OPTIONS with the engine alone, so
        both arguments default to the current HYPERVIEW settings.

        Args:
            engine: Owning Django template engine.
            resolver: Ordered raw-template resolver, or None to load the sources
                from the settings.
            validation: Validation policy for loaded source, or None to load it
                from the settings.
        """
        super().__init__(engine)
        self.resolver = (
            TemplateResolver.from_settings() if resolver is None else resolver
        )
        self.validation = (
            get_settings().validation if validation is None else validation
        )
        self._compiled = _CompiledCache()

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

        Yields:
            The one origin of the canonical template, or nothing when the resolver
            does not find it.
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

    def _caches_compiled_templates(self) -> bool:
        # An override may read ambient state, so only the loader's own steps are
        # shared between renders. An instance attribute, such as a patch, counts too.
        return (
            getattr(self.get_contents, "__func__", None) is ResolverLoader.get_contents
            and getattr(self.get_template_sources, "__func__", None)
            is ResolverLoader.get_template_sources
        )

    def get_template(
        self, template_name: str, skip: list[Origin] | None = None
    ) -> Template:
        """Return the compiled template, reusing it while its resolution is unchanged.

        A stored template is reused only when the resolution matches it in every
        field, including the content, so a source whose revision does not follow its
        content never serves stale code, and the template always carries the origin
        it would have been compiled with. Validation and compile errors are not
        stored. The compiled template is shared between threads, as in Django's
        cached loader, so its tags must keep no state on their nodes.

        Args:
            template_name: Canonical template name.
            skip: Origins to ignore, which stops a template extending itself.

        Returns:
            The compiled template.

        Raises:
            TemplateDoesNotExist: If no available origin yields a template.
        """
        if not self._caches_compiled_templates():
            return super().get_template(template_name, skip)
        tried = []
        for origin in self.get_template_sources(template_name):
            if skip is not None and origin in skip:
                tried.append((origin, "Skipped to avoid recursion"))
                continue
            resolved = origin.resolved
            key = (self.validation, resolved.source, resolved.name, resolved.origin)
            with self._compiled.lock:
                entry = self._compiled.entries.get(key)
                if entry is not None:
                    self._compiled.entries.move_to_end(key)
            if entry is not None and entry[0] == resolved:
                return entry[1]
            # The loader's own get_contents never reports a missing source: the
            # resolver already found it.
            contents = self.get_contents(origin)
            template = Template(contents, origin, origin.template_name, self.engine)
            self._remember_compiled(key, resolved, template)
            return template
        raise TemplateDoesNotExist(template_name, tried=tried)

    def _remember_compiled(
        self, key: _CompiledKey, resolved: ResolvedTemplate, template: Template
    ) -> None:
        size = len(resolved.content)
        with self._compiled.lock:
            previous = self._compiled.entries.pop(key, None)
            if previous is not None:
                self._compiled.size -= len(previous[0].content)
            if size > self._COMPILED_MAX_CHARS:
                return
            self._compiled.entries[key] = (resolved, template)
            self._compiled.size += size
            while (
                len(self._compiled.entries) > self._COMPILED_MAX_ENTRIES
                or self._compiled.size > self._COMPILED_MAX_CHARS
            ):
                _, (evicted, _) = self._compiled.entries.popitem(last=False)
                self._compiled.size -= len(evicted.content)

    def reset(self) -> None:
        """Forget the compiled templates, as Django does when a template changes."""
        with self._compiled.lock:
            self._compiled.entries.clear()
            self._compiled.size = 0
