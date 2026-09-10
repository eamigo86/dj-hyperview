"""Public, transport-independent hints for committed template invalidations."""

from dataclasses import dataclass

from django.dispatch import Signal

from .sources import canonicalize_template_name

__all__ = ["TemplateInvalidation", "template_invalidated"]


@dataclass(frozen=True, slots=True)
class TemplateInvalidation:
    """Canonical names affected by a commit on one database alias.

    Names are copied to a frozenset, including when the caller supplies a
    mutable iterable. The event carries no template content or authorization.
    """

    names: frozenset[str]
    using: str

    def __post_init__(self) -> None:
        """Validate the alias and copy canonical names into immutable storage.

        Raises:
            TypeError: If names is a string rather than a collection.
            ValueError: If using is not a non-empty database alias.
            InvalidTemplateName: If a name violates template-name rules.
        """
        if isinstance(self.names, (str, bytes)):
            raise TypeError("names must be a collection of template names")
        if not isinstance(self.using, str) or not self.using:
            raise ValueError("using must be a non-empty database alias")
        object.__setattr__(
            self,
            "names",
            frozenset(canonicalize_template_name(name) for name in self.names),
        )


template_invalidated = Signal()
"""Sent robustly after commit with sender=HyperviewTemplate and event=event."""
