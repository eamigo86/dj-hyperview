"""Hyperview request detection and Django middleware."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponseBase

from .http import HYPERVIEW_MEDIA_TYPE

HYPERVIEW_VERSION_HEADER = "X-Hyperview-Version"
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
            Whether verified Hyperview request metadata is present.
        """
        return self.is_hyperview


def detect_hyperview_request(request: HttpRequest) -> HyperviewRequestDetails:
    """Return typed Hyperview metadata derived from verified client headers.

    Args:
        request: Incoming Django request.

    Returns:
        Verified Hyperview request metadata.
    """
    version = request.headers.get(HYPERVIEW_VERSION_HEADER)
    accept = request.headers.get("Accept", "")
    explicit_media_types = {
        item.partition(";")[0].strip().lower() for item in accept.split(",")
    }
    accepts_hyperview = any(
        media_type in explicit_media_types and request.accepts(media_type)
        for media_type in _HYPERVIEW_MEDIA_TYPES
    )
    return HyperviewRequestDetails(version is not None or accepts_hyperview, version)


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
