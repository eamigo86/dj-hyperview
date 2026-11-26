"""Template source and permission stubs shared by the test modules."""

from typing import TYPE_CHECKING

from django.http import HttpRequest
from django.template.backends.django import DjangoTemplates

if TYPE_CHECKING:
    from dj_hyperview.sources import ResolvedTemplate


class CustomDjangoTemplates(DjangoTemplates):
    """Consumer Django backend subclass used by engine configuration tests."""


class TemplateSource:
    """Stub template source that resolves one fixed template body.

    Args:
        content: Template text to serve, or None to resolve nothing.
        revision: Revision reported for the served template.
    """

    def __init__(self, *, content: str | None = None, revision: str = "stub") -> None:
        """Store the template text and the revision."""
        self.content = content
        self.revision = revision

    def resolve(self, name: str) -> "ResolvedTemplate | None":
        """Resolve any name to the fixed content, or to nothing when none is set.

        Args:
            name: Template name to resolve.

        Returns:
            The resolved stub template, or None when the stub has no content.
        """
        if self.content is None:
            return None
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(
            name=name,
            content=self.content,
            origin=f"stub:{name}",
            source="stub",
            revision=self.revision,
        )


class CacheableTemplateSource(TemplateSource):
    """Stub source that opts in to raw-template caching."""

    cacheable = True


def validate_schema(document: str) -> None:
    """Accept every document, as a schema validator that never fails.

    Args:
        document: Schema document that is ignored.
    """
    del document


def allow_template_admin(request: HttpRequest) -> bool:
    """Allow authenticated staff in admin permission configuration tests.

    Args:
        request: Request whose user is checked.

    Returns:
        True when the request user is staff.
    """
    return bool(request.user.is_staff)
