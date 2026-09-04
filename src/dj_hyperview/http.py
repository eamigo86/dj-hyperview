"""HTTP responses for Hyperview markup."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.template.backends.django import Template
from django.template.response import TemplateResponse

from .conf import get_settings
from .engine import HyperviewEngine
from .validation import validate_rendered_hxml

HYPERVIEW_MEDIA_TYPE = "application/vnd.hyperview+xml"


class HyperviewResponse(HttpResponse):
    """An HTTP response that defaults to the Hyperview media type."""

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        **kwargs: Any,
    ) -> None:
        """Initialize a Hyperview media response.

        Args:
            content: Response body content.
            content_type: Explicit response media type.
            **kwargs: Additional Django response options.
        """
        super().__init__(content, content_type=content_type, **kwargs)


class HyperviewTemplateResponse(TemplateResponse):
    """A lazily rendered Django template response for Hyperview markup."""

    def __init__(
        self,
        request: HttpRequest | None,
        template: str | Sequence[str] | Template,
        context: dict[str, Any] | None = None,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        status: int | None = None,
        charset: str | None = None,
        using: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize a lazy Hyperview template response.

        Args:
            request: Request associated with template rendering.
            template: Template name, ordered names, or compiled template.
            context: Optional template context.
            content_type: Explicit response media type.
            status: Optional HTTP status.
            charset: Optional response charset.
            using: Optional Django template-engine alias.
            headers: Optional response headers.
        """
        super().__init__(
            request=request,
            template=template,
            context=context,
            content_type=content_type,
            status=status,
            charset=charset,
            using=using,
            headers=headers,
        )

    @property
    def rendered_content(self) -> str:
        """Render and validate the configured consumer template content.

        Returns:
            The rendered and validated Hyperview markup.
        """
        if (
            self.using is not None
            or not isinstance(self.template_name, (str, list, tuple))
            or not get_settings().sources
        ):
            return validate_rendered_hxml(super().rendered_content)
        context = self.resolve_context(self.context_data)
        return HyperviewEngine().render_hxml(self.template_name, context, self._request)
