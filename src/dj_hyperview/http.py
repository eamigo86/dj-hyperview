"""HTTP responses for Hyperview markup."""

import codecs
import zlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.template.backends.django import Template
from django.template.response import ContentNotRenderedError, TemplateResponse
from django.utils.http import parse_header_parameters

from .conf import get_settings
from .engine import _default_engine, _rendered_hxml_text, _ValidatedTemplate
from .exceptions import TemplateValidationError
from .validation import (
    _is_current_hxml_result,
    _validate_hxml_result,
    _ValidatedHxml,
)

HYPERVIEW_MEDIA_TYPE = "application/vnd.hyperview+xml"
HYPERVIEW_FRAGMENT_MEDIA_TYPE = "application/vnd.hyperview_fragment+xml"
_HYPERVIEW_MEDIA_TYPES = {
    HYPERVIEW_MEDIA_TYPE.casefold(): HYPERVIEW_MEDIA_TYPE,
    HYPERVIEW_FRAGMENT_MEDIA_TYPE.casefold(): HYPERVIEW_FRAGMENT_MEDIA_TYPE,
}


def _normalized_response_metadata(
    content_type: str | None, charset: str | None
) -> tuple[str | None, str | None]:
    media_type, parameters = parse_header_parameters(content_type or "")
    for declared in (charset or "utf-8", parameters.get("charset", "utf-8")):
        try:
            encoding = codecs.lookup(declared).name
        except LookupError as error:
            raise ValueError("Hyperview responses require UTF-8") from error
        if encoding != "utf-8":
            raise ValueError("Hyperview responses require UTF-8")
    normalized_media_type = _HYPERVIEW_MEDIA_TYPES.get(media_type.casefold())
    if normalized_media_type is None:
        return content_type, "utf-8"
    parameters.pop("charset", None)
    extra = [f"{key}={value}" for key, value in parameters.items()]
    extra.append("charset=utf-8")
    return "; ".join([normalized_media_type, *extra]), "utf-8"


_BODYLESS_STATUSES = frozenset({204, 205, 304})


class _HyperviewBody:
    """Validate complete candidates before atomically replacing response bytes."""

    _fragment = False
    _head_suppressed = False
    _validated_result: _ValidatedHxml | None = None
    _gzip_transport: bytes | None = None

    def _check_body_status(self, content: bytes) -> None:
        _normalized_response_metadata(self.headers.get("Content-Type"), self.charset)
        if (self.status_code in _BODYLESS_STATUSES) != (not content):
            raise TemplateValidationError(
                "http_body", "response body does not match its HTTP status"
            )

    def _candidate(self, value, *, prefix: bytes = b"") -> bytes:
        limit = get_settings().validation.max_bytes
        size = len(prefix)
        chunks = [prefix]
        iterable = hasattr(value, "__iter__") and not isinstance(
            value, (str, bytes, memoryview)
        )
        values = value if iterable else (value,)
        try:
            for piece in values:
                if not isinstance(piece, (str, bytes, memoryview)):
                    piece = str(piece)
                if len(piece) > limit - size:
                    raise TemplateValidationError(
                        "max_bytes", "document exceeds MAX_BYTES"
                    )
                try:
                    encoded = (
                        piece.encode("utf-8")
                        if isinstance(piece, str)
                        else bytes(piece)
                    )
                except UnicodeError as error:
                    raise TemplateValidationError(
                        "malformed_xml", "invalid XML"
                    ) from error
                size += len(encoded)
                if size > limit:
                    raise TemplateValidationError(
                        "max_bytes", "document exceeds MAX_BYTES"
                    )
                chunks.append(encoded)
        finally:
            if iterable and hasattr(value, "close"):
                value.close()
        return b"".join(chunks)

    def _commit(self, content: bytes) -> None:
        self._check_body_status(content)
        if self._gzip_transport is not None:
            self.headers.pop("Content-Length", None)
        self._gzip_transport = None
        self._validated_result = None
        self._container = [content]
        self.__dict__.pop("text", None)
        if isinstance(self, TemplateResponse):
            self._is_rendered = True

    def _commit_result(self, result: _ValidatedHxml) -> None:
        if not _is_current_hxml_result(result, fragment=self._fragment):
            result = _validate_hxml_result(result.text, fragment=self._fragment)
        self._commit(result.content)
        self._validated_result = result

    def _set_candidate(self, content: bytes) -> None:
        if self._gzip_transport is not None and self.has_header("Content-Encoding"):
            raise TemplateValidationError(
                "http_encoding", "remove Content-Encoding before changing the document"
            )
        self._check_body_status(content)
        if not content:
            self._commit(content)
            return
        try:
            document = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TemplateValidationError("malformed_xml", "invalid XML") from error
        self._commit_result(_validate_hxml_result(document, fragment=self._fragment))

    def _set_gzip_transport(self, content: bytes) -> bool:
        result = self._validated_result
        if result is None or not content.startswith(b"\x1f\x8b"):
            return False
        self._body_content()
        decoder = zlib.decompressobj(wbits=31)
        try:
            decoded = decoder.decompress(content, len(result.content) + 1)
        except zlib.error as error:
            raise TemplateValidationError(
                "http_encoding", "invalid gzip body"
            ) from error
        if (
            decoded != result.content
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise TemplateValidationError(
                "http_encoding", "gzip must preserve the validated representation"
            )
        if not _is_current_hxml_result(result, fragment=self._fragment):
            result = _validate_hxml_result(result.text, fragment=self._fragment)
        self._validated_result = result
        self._gzip_transport = content
        return True

    def _body_content(self) -> bytes:
        if isinstance(self, TemplateResponse) and not self._is_rendered:
            raise ContentNotRenderedError(
                "The response content must be rendered before it can be accessed."
            )
        content = b"".join(self._container)
        self._check_body_status(content)
        return content

    @property
    def content(self) -> bytes:
        content = self._body_content()
        if self._head_suppressed:
            return b""
        return self._gzip_transport if self._gzip_transport is not None else content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    @content.setter
    def content(self, value) -> None:
        if getattr(self, "_initializing_hyperview_template", False) and value == "":
            self._container = [b""]
            return
        candidate = self._candidate(value)
        if (
            not candidate
            and self._head_request
            and self.status_code not in _BODYLESS_STATUSES
            and getattr(self, "_container", None)
        ):
            # HEAD suppresses transport, never the already-validated representation.
            self._body_content()
            self._head_suppressed = True
            self.__dict__.pop("text", None)
            return
        if not self._set_gzip_transport(candidate):
            self._set_candidate(candidate)

    def _emission_content(self) -> bytes:
        content = self.content
        if self.status_code in _BODYLESS_STATUSES:
            return content
        encoding = self.headers.get("Content-Encoding", "").lower()
        if (self._gzip_transport is not None and encoding != "gzip") or (
            self._gzip_transport is None and encoding not in ("", "identity")
        ):
            raise TemplateValidationError(
                "http_encoding", "Content-Encoding does not match response transport"
            )
        return content

    def __iter__(self):
        return iter((self._emission_content(),))

    def serialize(self) -> bytes:
        """Serialize headers and guarded transport bytes.

        Returns:
            The complete HTTP message with coherent transport encoding.
        """
        content = self._emission_content()
        return self.serialize_headers() + b"\r\n\r\n" + content

    __bytes__ = serialize

    def write(self, content) -> None:
        self._set_candidate(self._candidate(content, prefix=self._body_content()))

    def writelines(self, lines) -> None:
        self._set_candidate(self._candidate(lines, prefix=self._body_content()))


class HyperviewResponse(_HyperviewBody, HttpResponse):
    """An HTTP response that defaults to the Hyperview media type."""

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_MEDIA_TYPE,
        charset: str | None = None,
        request: HttpRequest | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize a Hyperview media response.

        Args:
            content: Response body content.
            content_type: Explicit response media type.
            charset: Response character encoding.
            request: Optional request context for HEAD transport suppression.
            **kwargs: Additional Django response options.

        Raises:
            ValueError: If a Hyperview response selects a non-UTF-8 encoding.
        """
        content_type, charset = _normalized_response_metadata(content_type, charset)
        self._head_request = request is not None and request.method == "HEAD"
        super().__init__(content, content_type=content_type, charset=charset, **kwargs)


class HyperviewTemplateResponse(_HyperviewBody, TemplateResponse):
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
        self._head_request = request is not None and request.method == "HEAD"
        self._initializing_hyperview_template = True
        try:
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
        finally:
            del self._initializing_hyperview_template

    def _rendered_result(self) -> _ValidatedHxml | None:
        context = self.resolve_context(self.context_data)
        if self.using is None and isinstance(self.template_name, (str, list, tuple)):
            engine = _default_engine()
            if self.status_code not in _BODYLESS_STATUSES:
                return engine._render_result(
                    self.template_name, context, self._request, fragment=self._fragment
                )
            template = (
                engine.get_template(self.template_name)
                if isinstance(self.template_name, str)
                else engine.select_template(self.template_name)
            )
        else:
            template = self.resolve_template(self.template_name)
        if isinstance(template, _ValidatedTemplate):
            if self.status_code not in _BODYLESS_STATUSES:
                return template._render_result(
                    context, self._request, fragment=self._fragment
                )
            rendered = template._render_source(context, self._request)
        else:
            rendered = template.render(context, self._request)
        if self.status_code in _BODYLESS_STATUSES:
            self._check_body_status(self._candidate(rendered))
            return None
        return _validate_hxml_result(
            _rendered_hxml_text(rendered), fragment=self._fragment
        )

    @property
    def rendered_content(self) -> str:
        """Render fresh validated text without committing the lazy response.

        Returns:
            Validated template text, or empty text for a bodyless status.
        """
        result = self._rendered_result()
        return "" if result is None else result.text

    def render(self) -> HttpResponseBase:
        """Commit one private validated result, preserving Django callbacks.

        Returns:
            This response, or the replacement supplied by a Django callback.
        """
        result = self
        if not self._is_rendered:
            if (
                type(self).rendered_content
                is not HyperviewTemplateResponse.rendered_content
            ):
                self.content = self.rendered_content
            else:
                validated = self._rendered_result()
                if validated is None:
                    self._commit(b"")
                else:
                    self._commit_result(validated)
            for callback in self._post_render_callbacks:
                replacement = callback(result)
                if replacement is not None:
                    result = replacement
        return result


class HyperviewFragmentResponse(HyperviewResponse):
    """An eager HTTP response for one bare Hyperview fragment."""

    _fragment = True

    def __init__(
        self,
        content: str | bytes | memoryview | Iterable[str | bytes | memoryview] = b"",
        *,
        content_type: str | None = HYPERVIEW_FRAGMENT_MEDIA_TYPE,
        charset: str | None = None,
        request: HttpRequest | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize and validate a bare Hyperview fragment response.

        Args:
            content: Bare fragment response body.
            content_type: Explicit response media type.
            charset: Response character encoding.
            request: Optional request context for HEAD transport suppression.
            **kwargs: Additional Django response options.

        Raises:
            TemplateValidationError: If the fragment XML or root is invalid.
            ValueError: If a Hyperview response selects a non-UTF-8 encoding.
        """
        super().__init__(
            content,
            content_type=content_type,
            charset=charset,
            request=request,
            **kwargs,
        )


class HyperviewFragmentTemplateResponse(HyperviewTemplateResponse):
    """A lazy template response for one bare Hyperview fragment."""

    _fragment = True

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
