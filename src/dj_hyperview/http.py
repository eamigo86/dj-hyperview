"""HTTP responses for Hyperview markup."""

import codecs
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.template.backends.django import Template
from django.template.response import TemplateResponse

from .conf import get_settings
from .engine import HyperviewEngine, _ValidatedTemplate
from .validation import validate_rendered_hxml

HYPERVIEW_MEDIA_TYPE = "application/vnd.hyperview+xml"


def _normalized_response_metadata(
    content_type: str | None, charset: str | None
) -> tuple[str | None, str]:
    if content_type is None:
        return None, charset or "utf-8"
    media_type, separator, parameters = content_type.partition(";")
    if media_type.strip().casefold() != HYPERVIEW_MEDIA_TYPE:
        return content_type, charset or "utf-8"

    declared = charset or "utf-8"
    try:
        encoding = codecs.lookup(declared).name
    except LookupError as error:
        raise ValueError("Hyperview responses require UTF-8") from error
    if encoding != "utf-8":
        raise ValueError("Hyperview responses require UTF-8")

    extra = []
    if separator:
        for parameter in parameters.split(";"):
            candidate = parameter.strip()
            if candidate and not candidate.casefold().startswith("charset="):
                extra.append(candidate)
    extra.append("charset=utf-8")
    return "; ".join([HYPERVIEW_MEDIA_TYPE, *extra]), "utf-8"


class HyperviewResponse(HttpResponse):
    """An HTTP response that defaults to the Hyperview media type."""

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        charset: str | None = "utf-8",
        **kwargs: Any,
    ) -> None:
        """Initialize a Hyperview media response.

        Args:
            content: Response body content.
            content_type: Explicit response media type.
            charset: Response character encoding.
            **kwargs: Additional Django response options.

        Raises:
            ValueError: If a Hyperview response selects a non-UTF-8 encoding.
        """
        content_type, charset = _normalized_response_metadata(content_type, charset)
        super().__init__(content, content_type=content_type, charset=charset, **kwargs)


class HyperviewTemplateResponse(TemplateResponse):
    """A lazily rendered Django template response for Hyperview markup."""

    def __init__(
        self,
        request: HttpRequest | None,
        template: str | Sequence[str] | Template,
        context: dict[str, Any] | None = None,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        status: int | None = None,
        charset: str | None = "utf-8",
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

        Raises:
            ValueError: If a Hyperview response selects a non-UTF-8 encoding.
        """
        content_type, charset = _normalized_response_metadata(content_type, charset)
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
        if isinstance(self.template_name, _ValidatedTemplate):
            return super().rendered_content
        if (
            self.using is not None
            or not isinstance(self.template_name, (str, list, tuple))
            or not get_settings().sources
        ):
            return validate_rendered_hxml(super().rendered_content)
        context = self.resolve_context(self.context_data)
        return HyperviewEngine().render_hxml(self.template_name, context, self._request)
