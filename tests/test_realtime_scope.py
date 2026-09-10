"""The response must survive Django's sync middleware adaptation."""

import asyncio

from django.core.handlers.asgi import ASGIHandler
from django.test import override_settings
from django.urls import path

from tests.test_realtime_response import Events

urlpatterns = []


def test_adapted_async_view_retains_stream_until_real_asgi_completion():
    from dj_hyperview.realtime import realtime_asgi, sse_response

    async def run():
        calls = []
        sent = []
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})

        async def close():
            calls.append(1)

        async def view(request):
            return sse_response(
                Events([{"event": "resync", "data": {"version": 1}}]), aclose=close
            )

        async def send(message):
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


def test_public_close_started_before_app_return_stays_owned_until_release():
    from dj_hyperview.realtime import realtime_asgi, sse_response

    async def run():
        release_entered = asyncio.Event()
        release_allowed = asyncio.Event()
        release_finished = asyncio.Event()
        application_returning = asyncio.Event()
        calls = 0

        async def release():
            nonlocal calls
            calls += 1
            release_entered.set()
            await release_allowed.wait()
            release_finished.set()

        async def events():
            yield {"event": "resync", "data": {"version": 1}}

        async def application(scope, receive, send):
            response = sse_response(events(), aclose=release)
            response.close()  # Public synchronous Django API, on owning loop.
            await release_entered.wait()
            application_returning.set()

        task = asyncio.create_task(
            realtime_asgi(application)({"type": "http"}, None, None)
        )
        await asyncio.wait_for(application_returning.wait(), timeout=1)
        try:
            # Deadline is only a bounded liveness oracle, not transport timing.
            done, _ = await asyncio.wait({task}, timeout=0.05)
            returned_before_release = bool(done) and not release_finished.is_set()
        finally:
            release_allowed.set()
            await asyncio.wait_for(task, timeout=1)
            await asyncio.wait_for(release_finished.wait(), timeout=1)

        assert calls == 1
        assert not returned_before_release, (
            "realtime_asgi returned while public response.close cleanup was pending"
        )

    asyncio.run(run())
