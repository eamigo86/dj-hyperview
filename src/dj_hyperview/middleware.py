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
        return self.is_hyperview


def detect_hyperview_request(request: HttpRequest) -> HyperviewRequestDetails:
    """Return typed Hyperview metadata derived from verified client headers."""
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
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    def __call__(
        self, request: HttpRequest
    ) -> HttpResponseBase | Awaitable[HttpResponseBase]:
        if self.async_mode:
            return self.__acall__(request)
        _attach_hyperview(request)
        return self.get_response(request)

    async def __acall__(self, request: HttpRequest) -> HttpResponseBase:
        _attach_hyperview(request)
        return await self.get_response(request)
