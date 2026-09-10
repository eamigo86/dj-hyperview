"""Public ASGI request lifetime ownership across Django's sync adapters."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any

_Application = Callable[..., Awaitable[None]]
_LOGGER = logging.getLogger("dj_hyperview.realtime")


class _Scope:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.closed = False
        self.callbacks: set[Callable[[], Awaitable[None]]] = set()

    async def close(self) -> None:
        callbacks = tuple(self.callbacks)
        self.callbacks.clear()
        results = await asyncio.gather(
            *(call() for call in callbacks), return_exceptions=True
        )
        if any(isinstance(result, BaseException) for result in results):
            _LOGGER.warning("realtime_scope_cleanup_failed")


_CURRENT: ContextVar[_Scope | None] = ContextVar("realtime_asgi_owner", default=None)


def _register(callback: Callable[[], Awaitable[None]]) -> Callable[[], None]:
    scope = _CURRENT.get()
    if scope is None or scope.closed or scope.loop is not asyncio.get_running_loop():
        raise RuntimeError("SSE response requires an active realtime ASGI scope")
    scope.callbacks.add(callback)
    return lambda: scope.callbacks.discard(callback)


def realtime_asgi(application: _Application) -> _Application:
    """Own SSE cleanup for each real HTTP ASGI request, including adapted views.

    Wrap the application's outer ASGI callable once. Non-HTTP scopes pass
    through unchanged. The mutable ownership scope propagates through Django's
    sync/async adapters; view-task completion is not request completion.

    Args:
        application: The ASGI application, normally get_asgi_application().

    Returns:
        An ASGI callable that closes all remaining SSE owners in finally.
    """

    async def wrapped(
        scope: Mapping[str, Any], receive: Callable, send: Callable
    ) -> None:
        if scope["type"] != "http":
            await application(scope, receive, send)
            return
        owner = _Scope()
        token = _CURRENT.set(owner)
        try:
            await application(scope, receive, send)
        finally:
            owner.closed = True
            cleanup = asyncio.create_task(owner.close())
            cancelled = False
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
            finally:
                _CURRENT.reset(token)
            if cancelled:
                raise asyncio.CancelledError

    return wrapped
