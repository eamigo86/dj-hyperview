"""Hyperview request detection and Django middleware."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponseBase

from .http import HYPERVIEW_MEDIA_TYPE

HYPERVIEW_VERSION_HEADER = "X-Hyperview-Version"
_VERSION_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
_HYPERVIEW_MEDIA_TYPES = (
    HYPERVIEW_MEDIA_TYPE,
    "application/vnd.hyperview_fragment+xml",
)


@dataclass(frozen=True, slots=True)
class HyperviewRequestDetails:
    """Typed metadata attached to a request by HyperviewMiddleware."""

    is_hyperview: bool
    version: str | None = None

    def __bool__(self) -> bool:
        """Return whether the request is from a Hyperview client.

        Returns:
            Whether the request claims to come from a Hyperview client.
        """
        return self.is_hyperview


def detect_hyperview_request(request: HttpRequest) -> HyperviewRequestDetails:
    """Return typed Hyperview metadata derived from claimed client headers.

    Headers are client-controlled, so the result identifies a claimed client and
    grants no trust. A version header marks the request as Hyperview, but its
    value is kept only when it is 1 to 64 characters long, starts with a letter
    or digit, and continues with letters, digits, dots, plus signs, underscores
    or hyphens. Any other value leaves the version unset.

    Args:
        request: Incoming Django request.

    Returns:
        Claimed Hyperview request metadata.
    """
    claimed = request.headers.get(HYPERVIEW_VERSION_HEADER)
    version = claimed if claimed and _VERSION_TOKEN.fullmatch(claimed) else None
    accept = request.headers.get("Accept", "")
    explicit_media_types = {
        item.partition(";")[0].strip().lower() for item in accept.split(",")
    }
    try:
        accepts_hyperview = any(
            media_type in explicit_media_types and request.accepts(media_type)
            for media_type in _HYPERVIEW_MEDIA_TYPES
        )
    except ValueError:
        # Django refuses to parse an Accept token longer than MAX_HEADER_LENGTH.
        accepts_hyperview = False
    return HyperviewRequestDetails(claimed is not None or accepts_hyperview, version)


def _attach_hyperview(request: HttpRequest) -> None:
    request.hyperview = detect_hyperview_request(request)


class HyperviewMiddleware:
    """Attach typed Hyperview request details without altering the response."""

    sync_capable = True
    async_capable = True

    def __init__(
        self,
        get_response: Callable[
            [HttpRequest], HttpResponseBase | Awaitable[HttpResponseBase]
        ],
    ) -> None:
        """Initialize request detection middleware.

        Args:
            get_response: Downstream sync or async response callable.
        """
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    def __call__(
        self, request: HttpRequest
    ) -> HttpResponseBase | Awaitable[HttpResponseBase]:
        """Attach request metadata and invoke the downstream callable.

        Args:
            request: Incoming Django request.

        Returns:
            The downstream response or awaitable response.
        """
        if self.async_mode:
            return self.__acall__(request)
        _attach_hyperview(request)
        return self.get_response(request)

    async def __acall__(self, request: HttpRequest) -> HttpResponseBase:
        """Attach request metadata and await the downstream callable.

        Args:
            request: Incoming Django request.

        Returns:
            The awaited downstream response.
        """
        _attach_hyperview(request)
        return await self.get_response(request)
