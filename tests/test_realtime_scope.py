"""The response must survive Django's sync middleware adaptation."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from django.core.handlers.asgi import ASGIHandler
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.test import override_settings
from django.urls import path

from tests.test_realtime_response import Events

urlpatterns = []


def test_adapted_async_view_retains_stream_until_real_asgi_completion() -> None:
    """Adapted async view retains stream until real ASGI completion."""
    from dj_hyperview.realtime import realtime_asgi, sse_response

    async def run() -> None:
        calls = []
        sent = []
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})

        async def close() -> None:
            calls.append(1)

        async def view(request: HttpRequest) -> HttpResponseBase:
            return sse_response(
                Events([{"event": "resync", "data": {"version": 1}}]), aclose=close
            )

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        global urlpatterns
        urlpatterns = [path("events", view)]
        with override_settings(
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=["testserver"],
            MIDDLEWARE=["tests.test_realtime_response.SyncMiddleware"],
        ):
            application = realtime_asgi(ASGIHandler())
            await application(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/events",
                    "query_string": b"",
                    "headers": [(b"host", b"testserver")],
                },
                incoming.get,
                send,
            )
        assert (
            b"".join(item.get("body", b"") for item in sent)
            == b'event: resync\ndata: {"version":1}\n\n'
        )
        assert calls == [1]

    asyncio.run(run())


def test_public_close_started_before_app_return_stays_owned_until_release() -> None:
    """Public close started before app return stays owned until release."""
    from dj_hyperview.realtime import realtime_asgi, sse_response

    async def run() -> None:
        release_entered = asyncio.Event()
        release_allowed = asyncio.Event()
        release_finished = asyncio.Event()
        application_returning = asyncio.Event()
        calls = 0

        async def release() -> None:
            nonlocal calls
            calls += 1
            release_entered.set()
            await release_allowed.wait()
            release_finished.set()

        async def events() -> AsyncIterator[dict[str, Any]]:
            yield {"event": "resync", "data": {"version": 1}}

        async def application(
            scope: dict[str, Any],
            receive: object,
            send: Callable[[dict[str, Any]], Awaitable[None]],
        ) -> None:
            response = sse_response(events(), aclose=release)
            response.close()  # Public synchronous Django API, on owning loop.
            await release_entered.wait()
            application_returning.set()

        task = asyncio.create_task(
            realtime_asgi(application)({"type": "http"}, None, None)
        )
        await asyncio.wait_for(application_returning.wait(), timeout=1)
        try:
            # A fixed number of loop turns, not a wall-clock window: cleanup is
            # blocked on release_allowed, so only a premature return can end the task.
            for _ in range(50):
                await asyncio.sleep(0)
            returned_before_release = task.done() and not release_finished.is_set()
        finally:
            release_allowed.set()
            await asyncio.wait_for(task, timeout=1)
            await asyncio.wait_for(release_finished.wait(), timeout=1)

        assert calls == 1
        assert not returned_before_release, (
            "realtime_asgi returned while public response.close cleanup was pending"
        )

    asyncio.run(run())
