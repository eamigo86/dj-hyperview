"""Shared template source contracts."""

from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol, runtime_checkable
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
    False.
    """

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve a canonical name or report a source-local miss.

        Args:
            name: Canonical template name.

        Returns:
            The resolved template when found, otherwise a source-local miss.
        """
        ...


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
