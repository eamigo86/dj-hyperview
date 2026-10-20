"""Django loader backed by the dj-hyperview resolver."""

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
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
# What a loader keeps for its compiled templates, which a deep copy starts again.
_COMPILED_CACHE = ("_compiled", "_compiled_size", "_compiled_lock")


def _without_compiled_cache(state: object) -> object:
    # The state of an object is its dict, or a (dict, slots) pair when it has slots.
    if isinstance(state, tuple) and len(state) == 2:
        return _without_compiled_cache(state[0]), state[1]
    if isinstance(state, dict):
        return {k: v for k, v in state.items() if k not in _COMPILED_CACHE}
    return state


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
        self._start_compiled_cache()

    def _start_compiled_cache(self) -> None:
        self._compiled: OrderedDict[_CompiledKey, tuple[ResolvedTemplate, Template]] = (
            OrderedDict()
        )
        self._compiled_size = 0
        self._compiled_lock = threading.Lock()

    def __deepcopy__(self, memo: dict[int, object]) -> "ResolverLoader":
        """Copy the loader with an empty compiled-template cache.

        The lock cannot be copied and the compiled templates belong to the original,
        so the copy gets a cache and a lock of its own and compiles its templates
        again. Everything else is copied as the default protocol would copy it: the
        dict and the slots, or the state of a subclass's own __getstate__, which its
        __setstate__ then restores. The copy is as independent as a copy of any other
        Django loader.

        Args:
            memo: The memo of the running deep copy.

        Returns:
            An independent loader with an empty compiled-template cache.
        """
        duplicate = object.__new__(type(self))
        # Registered first, as the default protocol does: the engine and the
        # templates this loader reaches lead back to it.
        memo[id(self)] = duplicate
        state = deepcopy(_without_compiled_cache(self.__getstate__()), memo)
        # Restored as copy restores it. The cache starts last, so a __setstate__
        # that replaces the dict of the copy cannot leave it without one.
        if state is not None:
            if hasattr(duplicate, "__setstate__"):
                duplicate.__setstate__(state)
            else:
                state, slots = state if isinstance(state, tuple) else (state, {})
                vars(duplicate).update(state or {})
                for name, value in slots.items():
                    setattr(duplicate, name, value)
        duplicate._start_compiled_cache()
        return duplicate

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
            with self._compiled_lock:
                entry = self._compiled.get(key)
                if entry is not None:
                    self._compiled.move_to_end(key)
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
        with self._compiled_lock:
            previous = self._compiled.pop(key, None)
            if previous is not None:
                self._compiled_size -= len(previous[0].content)
            if size > self._COMPILED_MAX_CHARS:
                return
            self._compiled[key] = (resolved, template)
            self._compiled_size += size
            while (
                len(self._compiled) > self._COMPILED_MAX_ENTRIES
                or self._compiled_size > self._COMPILED_MAX_CHARS
            ):
                _, (evicted, _) = self._compiled.popitem(last=False)
                self._compiled_size -= len(evicted.content)

    def reset(self) -> None:
        """Forget the compiled templates, as Django does when a template changes."""
        with self._compiled_lock:
            self._compiled.clear()
            self._compiled_size = 0
