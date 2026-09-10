"""Closed SSE framing and loop-owned cleanup through public Django APIs."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from django.http import StreamingHttpResponse

from ._events import encode_event
from ._scope import _register

_LOGGER = logging.getLogger("dj_hyperview.realtime")
_CLOSE_TIMEOUT = 2.0


class _OwnedEvents:
    def __init__(
        self,
        events: AsyncIterator[Mapping[str, object] | None],
        release: Callable[[], Awaitable[None]],
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._events = aiter(events)
        self._release = release
        self._closing: asyncio.Task[None] | None = None
        self._unregister = _register(self.aclose)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._closing is not None:
            raise StopAsyncIteration
        try:
            return encode_event(await anext(self._events))
        except BaseException:
            await self.aclose()
            raise

    async def _call_close(self, callback: Callable[[], Awaitable[None]]) -> None:
        try:
            await callback()
        except Exception:
            _LOGGER.warning("realtime_cleanup_failed")

    async def _finish(self) -> None:
        try:
            # Release does not depend on an iterator's finally running (or finishing).
            callbacks = [self._release]
            iterator_close = getattr(self._events, "aclose", None)
            if iterator_close is not None:
                callbacks.append(iterator_close)
            tasks = [asyncio.create_task(self._call_close(call)) for call in callbacks]
            _, pending = await asyncio.wait(tasks, timeout=_CLOSE_TIMEOUT)
            if pending:
                _LOGGER.warning("realtime_cleanup_timeout")
                for task in pending:
                    task.cancel()
                # Give cancellation-cooperative finalizers a turn; do not wait forever.
                await asyncio.sleep(0)
        finally:
            self._unregister()

    async def aclose(self) -> None:
        if self._closing is None:
            self._closing = self._loop.create_task(self._finish())
        await asyncio.shield(self._closing)

    def close(self) -> None:
        if not self._loop.is_running():
            _LOGGER.warning("realtime_cleanup_loop_unavailable")
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            self._loop.create_task(self.aclose())
        else:
            future = asyncio.run_coroutine_threadsafe(self.aclose(), self._loop)
            try:
                future.result(timeout=_CLOSE_TIMEOUT + 0.1)
            except Exception:
                _LOGGER.warning("realtime_cleanup_wait_failed")


def sse_response(
    async_events: AsyncIterator[Mapping[str, object] | None],
    *,
    aclose: Callable[[], Awaitable[None]],
) -> StreamingHttpResponse:
    """Transfer an async event stream and its owner to a Django SSE response.

    Construct inside an active realtime_asgi HTTP scope on the live loop that
    owns the subscription. Django sync/async middleware adaptation is supported.
    The explicit owner callback runs once, even if iteration never begins.
    Cleanup is cancellation-cooperative and bounded to two seconds; failures
    emit only a fixed log code. Callers retain ownership if construction fails.

    Args:
        async_events: Closed event envelopes, or None for a heartbeat comment.
        aclose: Idempotent asynchronous owner cleanup; release admission in finally.

    Returns:
        An async StreamingHttpResponse with SSE and no-buffering headers.

    Raises:
        RuntimeError: If called outside an active same-loop realtime ASGI scope.
        TypeError: If events are not async iterable or cleanup is not callable.
    """
    if not callable(aclose):
        raise TypeError("aclose must be callable")
    content = _OwnedEvents(async_events, aclose)
    return StreamingHttpResponse(
        content,
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
