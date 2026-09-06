"""HTTP responses for Hyperview markup."""

import codecs
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.template.backends.django import Template
from django.template.response import TemplateResponse
from django.utils.http import parse_header_parameters

from .engine import HyperviewEngine, _ValidatedTemplate
from .exceptions import TemplateValidationError
from .validation import validate_fragment_hxml, validate_rendered_hxml

HYPERVIEW_MEDIA_TYPE = "application/vnd.hyperview+xml"
HYPERVIEW_FRAGMENT_MEDIA_TYPE = "application/vnd.hyperview_fragment+xml"
_HYPERVIEW_MEDIA_TYPES = {
    HYPERVIEW_MEDIA_TYPE.casefold(): HYPERVIEW_MEDIA_TYPE,
    HYPERVIEW_FRAGMENT_MEDIA_TYPE.casefold(): HYPERVIEW_FRAGMENT_MEDIA_TYPE,
}


def _normalized_response_metadata(
    content_type: str | None, charset: str | None
) -> tuple[str | None, str | None]:
    if content_type is None:
        return None, charset
    media_type, parameters = parse_header_parameters(content_type)
    normalized_media_type = _HYPERVIEW_MEDIA_TYPES.get(media_type.casefold())
    if normalized_media_type is None:
        return content_type, charset

    for declared in (charset or "utf-8", parameters.pop("charset", "utf-8")):
        try:
            encoding = codecs.lookup(declared).name
        except LookupError as error:
            raise ValueError("Hyperview responses require UTF-8") from error
        if encoding != "utf-8":
            raise ValueError("Hyperview responses require UTF-8")

    extra = [f"{key}={value}" for key, value in parameters.items()]
    extra.append("charset=utf-8")
    return "; ".join([normalized_media_type, *extra]), "utf-8"


class HyperviewResponse(HttpResponse):
    """An HTTP response that defaults to the Hyperview media type."""

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        charset: str | None = None,
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
        if self.using is not None or not isinstance(
            self.template_name, (str, list, tuple)
        ):
            return validate_rendered_hxml(super().rendered_content)
        context = self.resolve_context(self.context_data)
        return HyperviewEngine().render_hxml(self.template_name, context, self._request)


def _fragment_content(response: HttpResponse) -> str:
    try:
        return response.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TemplateValidationError("malformed_xml", "invalid XML") from error


class HyperviewFragmentResponse(HyperviewResponse):
    """An eager HTTP response for one bare Hyperview fragment."""

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_FRAGMENT_MEDIA_TYPE,
        charset: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize and validate a bare Hyperview fragment response.

        Args:
            content: Bare fragment response body.
            content_type: Explicit response media type.
            charset: Response character encoding.
            **kwargs: Additional Django response options.

        Raises:
            TemplateValidationError: If the fragment XML or root is invalid.
            ValueError: If a Hyperview response selects a non-UTF-8 encoding.
        """
        super().__init__(
            content,
            content_type=content_type,
            charset=charset,
            **kwargs,
        )
        validate_fragment_hxml(_fragment_content(self))


class HyperviewFragmentTemplateResponse(HyperviewTemplateResponse):
    """A lazy template response for one bare Hyperview fragment."""

    def __init__(
        self,
        request: HttpRequest | None,
        template: str | Sequence[str] | Template,
        context: dict[str, Any] | None = None,
        content_type: str | None = HYPERVIEW_FRAGMENT_MEDIA_TYPE,
        status: int | None = None,
        charset: str | None = None,
        using: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize a lazy bare-fragment template response.

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
        """Render and validate the configured bare fragment.

        Returns:
            The rendered fragment with a client-safe root.

        Raises:
            TemplateValidationError: If the fragment XML or root is invalid.
        """
        return validate_fragment_hxml(super().rendered_content)
