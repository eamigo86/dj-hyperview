"""Shared template source contracts."""

from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol, runtime_checkable
from unicodedata import category

from dj_hyperview.exceptions import InvalidTemplateName


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
    """A backend that may resolve a canonical template name."""

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve a canonical name or report a source-local miss.

        Args:
            name: Canonical template name.
        """
        ...


def canonicalize_template_name(name: str) -> str:
    """Return a canonical consumer-owned template name.

    Args:
        name: Candidate template name.
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
        or any(category(character) in {"Cc", "Cs"} for character in name)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise InvalidTemplateName(name)
    return name
