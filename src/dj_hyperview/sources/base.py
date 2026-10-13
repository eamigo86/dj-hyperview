"""Shared template source contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any, Protocol, runtime_checkable
from unicodedata import category

from dj_hyperview.exceptions import InvalidTemplateName

# Control, format, surrogate, line separator and paragraph separator characters.
# Format characters such as bidirectional overrides, zero-width characters and tag
# characters are invisible or reorder text, so two names can look identical, or
# hide an extension, in logs and error pages.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


@dataclass(frozen=True, slots=True)
class ResolvedTemplate:
    """Immutable template content and its source identity."""

    name: str
    content: str
    origin: str
    source: str
    revision: str


@runtime_checkable
class TemplateSource(Protocol):
    """A backend that may resolve a canonical template name.

    Only resolve is required. A source may also expose an optional cacheable
    attribute, as a class attribute, instance attribute or property. Raw-template
    caching is an explicit opt-in: the resolver caches a source only when
    cacheable is exactly True, so a source that omits it, or sets any other value,
    is never cached. Opt in only when the output depends solely on the template
    name and the constructor options. A source that reads ambient state, such as
    the current tenant, user, request or language, must not opt in, because cached
    content would be served to a different caller. Subclasses inherit the
    attribute; a subclass that changes what resolve reads must set cacheable to
    False. The built-in sources are stricter: they report cacheable only while
    resolve is their own, so a subclass that overrides resolve must declare
    cacheable True itself.

    The result of resolve is checked once by the resolver, whether or not the
    source is cached. It must be None or a ResolvedTemplate whose name equals the
    requested canonical name and whose five fields are all str (a str subclass is
    fine). Anything else raises SourceUnavailable instead of being used or cached,
    because the cache cannot read such an entry back.
    """

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve a canonical name or report a source-local miss.

        Args:
            name: Canonical template name.

        Returns:
            The resolved template, named exactly name, or None for a source-local
            miss.
        """
        ...


class _BuiltinCacheable:
    """The cacheable attribute of a built-in source, shadowed by any assignment.

    This is a non-data descriptor, so an instance attribute takes precedence and
    assignment, deletion and mock.patch.object behave as they do for a plain class
    attribute. Without an assignment the source is cacheable by default only while
    it runs the resolve of the built-in class that holds this descriptor. A
    subclass that overrides resolve may read ambient state, such as the current
    tenant, so it is not cached until it declares cacheable itself, as a class
    attribute or by assigning the instance attribute.
    """

    def __init__(self, default: Callable[[Any], bool] = lambda source: True) -> None:
        self._default = default

    def __set_name__(self, owner: type, name: str) -> None:
        self._owner = owner

    def __get__(self, source: Any, owner: type | None = None) -> Any:
        if source is None:
            return self
        return type(source).resolve is self._owner.resolve and self._default(source)


def canonicalize_template_name(name: str) -> str:
    """Return a canonical consumer-owned template name.

    A canonical name is a relative POSIX path. Absolute and Windows drive paths,
    empty or dot segments, backslashes, NUL, and control, format, surrogate, line
    separator and paragraph separator characters are rejected, never normalized.

    Args:
        name: Candidate template name.

    Returns:
        The unchanged canonical template name.

    Raises:
        InvalidTemplateName: If the candidate is unsafe or noncanonical.
    """
    if not isinstance(name, str):
        raise InvalidTemplateName(name)

    parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or PureWindowsPath(name).drive
        or "\\" in name
        or "\0" in name
        or any(category(character) in _UNSAFE_CATEGORIES for character in name)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise InvalidTemplateName(name)
    return name
