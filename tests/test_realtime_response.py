"""Real Django ASGI framing and pre-iteration ownership regressions."""

import asyncio
import gc
import weakref

import pytest
from django.core.handlers.asgi import ASGIHandler
from django.test import override_settings
from django.urls import path

urlpatterns = []


def response_api():
    from dj_hyperview.realtime import sse_response

    return sse_response


def run_in_scope(run):
    from dj_hyperview.realtime import realtime_asgi

    async def application(scope, receive, send):
        await run()

    asyncio.run(realtime_asgi(application)({"type": "http"}, None, None))


def async_application(application):
    from dj_hyperview.realtime import realtime_asgi

    return realtime_asgi(application)


class SyncMiddleware:
    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)


class Events:
    def __init__(self, values=(), *, failure=None, blocked=False):
        self.values = iter(values)
        self.failure = failure
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.closed = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.entered.set()
        if self.blocked:
            await asyncio.Event().wait()
        if self.failure:
            raise self.failure
        try:
            return next(self.values)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.closed += 1


def test_frames_headers_and_cleanup_are_real_django_response():
    async def run():
        events = Events(
            [
                None,
                {"event": "resync", "data": {"version": 1}},
                {"event": "invalidate", "data": {"version": 1, "resources": ["tasks"]}},
                {"event": "auth-required", "data": {"version": 1}},
            ]
        )
        calls = []

        async def release():
            calls.append("released")

        response = response_api()(events, aclose=release)
        assert response.is_async
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache, no-transform"
        assert response["X-Accel-Buffering"] == "no"
        frames = [part async for part in response.streaming_content]
        assert frames == [
            b": heartbeat\n\n",
            b'event: resync\ndata: {"version":1}\n\n',
            b'event: invalidate\ndata: {"version":1,"resources":["tasks"]}\n\n',
            b'event: auth-required\ndata: {"version":1}\n\n',
        ]
        await asyncio.to_thread(response.close)
        assert calls == ["released"] and events.closed == 1

    run_in_scope(run)


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"event": "resync", "data": {"version": True}},
        {"event": "resync", "data": {"version": 1}, "id": "secret"},
        {"event": "resync\ninjected", "data": {"version": 1}},
        {"event": "resync", "data": {"version": 1, "resources": ["tasks"]}},
        {"event": "invalidate", "data": {"version": 1, "resources": []}},
        {
            "event": "invalidate",
            "data": {"version": 1, "resources": ["tasks", "tasks"]},
        },
        {
            "event": "invalidate",
            "data": {"version": 1, "resources": ["https://secret"]},
        },
        {"event": "invalidate", "data": {"version": 1, "resources": ["x" * 65]}},
        {
            "event": "invalidate",
            "data": {"version": 1, "resources": [f"r{i}" for i in range(33)]},
        },
        {"event": "invalidate", "data": {"version": 1, "resources": ["é"]}},
        {"event": "invalidate", "data": {"version": 1, "resources": "tasks"}},
    ],
)
def test_invalid_closed_envelopes_never_emit_payload_and_release(event):
    async def run():
        events = Events([event])
        released = asyncio.Event()

        async def release():
            released.set()

        response = response_api()(events, aclose=release)
        with pytest.raises(ValueError, match="Invalid realtime event") as caught:
            await anext(response.streaming_content)
        assert "secret" not in str(caught.value)
        assert released.is_set() and events.closed == 1

    run_in_scope(run)


@pytest.mark.parametrize("threaded", [True, False])
def test_never_iterated_close_transfers_ownership_and_is_idempotent(threaded):
    async def run():
        events = Events(blocked=True)
        calls = []
        released = asyncio.Event()

        async def release():
            calls.append(1)
            released.set()

        response = response_api()(events, aclose=release)
        if threaded:
            await asyncio.to_thread(response.close)
        else:
            response.close()
        await asyncio.wait_for(released.wait(), 1)
        await asyncio.to_thread(response.close)
        assert not events.entered.is_set()
        assert calls == [1] and events.closed == 1

    run_in_scope(run)


@pytest.mark.parametrize("cancel", [False, True])
def test_iterator_failure_or_cancellation_releases_exactly_once(cancel):
    async def run():
        error = RuntimeError("upstream failure")
        events = Events(blocked=cancel, failure=None if cancel else error)
        calls = []

        async def release():
            calls.append(1)

        response = response_api()(events, aclose=release)
        task = asyncio.create_task(anext(response.streaming_content))
        await events.entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        await asyncio.to_thread(response.close)
        assert calls == [1] and events.closed == 1

    run_in_scope(run)


def test_real_asgi_disconnect_before_first_frame_releases_unstarted_owner():
    async def run():
        events = Events(blocked=True)
        calls = []
        started = asyncio.Event()
        receive_queue = asyncio.Queue()
        await receive_queue.put(
            {"type": "http.request", "body": b"", "more_body": False}
        )

        async def release():
            calls.append(1)

        async def view(request):
            return response_api()(events, aclose=release)

        async def send(message):
            assert message["type"] == "http.response.start"
            assert message["status"] == 200
            started.set()
            await asyncio.Event().wait()

        global urlpatterns
        urlpatterns = [path("events", view)]
        with override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"]):
            handler = async_application(ASGIHandler())
            task = asyncio.create_task(
                handler(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0"},
                        "http_version": "1.1",
                        "method": "GET",
                        "scheme": "http",
                        "path": "/events",
                        "query_string": b"",
                        "headers": [(b"host", b"testserver")],
                    },
                    receive_queue.get,
                    send,
                )
            )
            await asyncio.wait_for(started.wait(), 1)
            await receive_queue.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
        assert not events.entered.is_set()
        assert calls == [1] and events.closed == 1

    run_in_scope(run)


@pytest.mark.parametrize("timeout", [False, True])
def test_cleanup_failure_is_bounded_redacted_and_does_not_skip_owner(timeout, caplog):
    async def run():
        events = Events()
        released = asyncio.Event()
        calls = []

        async def release():
            calls.append(1)
            try:
                if timeout:
                    await asyncio.Event().wait()
                raise RuntimeError("redis://private-credential/topic")
            finally:
                released.set()

        response = response_api()(events, aclose=release)
        await asyncio.wait_for(asyncio.to_thread(response.close), 2.5)
        await asyncio.wait_for(released.wait(), 0.2)
        await asyncio.to_thread(response.close)
        assert calls == [1] and events.closed == 1
        assert "private-credential" not in caplog.text
        assert "realtime_cleanup_" in caplog.text

    run_in_scope(run)


def test_response_requires_live_loop_and_explicit_owner():
    with pytest.raises(RuntimeError):
        response_api()(Events(), aclose=lambda: None)
    with pytest.raises(TypeError):
        response_api()(Events())


@pytest.mark.parametrize("cancel", [False, True])
def test_real_asgi_send_error_or_external_cancel_before_iteration_releases(cancel):
    async def run():
        events = Events(blocked=True)
        released = asyncio.Event()
        started = asyncio.Event()
        receives = asyncio.Queue()
        await receives.put({"type": "http.request", "body": b"", "more_body": False})

        async def release():
            released.set()

        async def view(request):
            return response_api()(events, aclose=release)

        async def send(message):
            started.set()
            if cancel:
                await asyncio.Event().wait()
            raise OSError("closed transport")

        global urlpatterns
        urlpatterns = [path("events", view)]
        with override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"]):
            task = asyncio.create_task(
                async_application(ASGIHandler())(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0"},
                        "http_version": "1.1",
                        "method": "GET",
                        "scheme": "http",
                        "path": "/events",
                        "query_string": b"",
                        "headers": [(b"host", b"testserver")],
                    },
                    receives.get,
                    send,
                )
            )
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else OSError):
                await task
        await asyncio.wait_for(released.wait(), 0.5)
        assert released.is_set(), (
            "ASGI task ended before first iteration without owner cleanup"
        )
        assert not events.entered.is_set() and events.closed == 1

    run_in_scope(run)


@pytest.mark.parametrize("mixed", [False, True])
def test_normal_asgi_completion_releases_and_detaches_request_owner(mixed):
    async def run():
        calls = []
        references = []
        received = asyncio.Queue()
        await received.put({"type": "http.request", "body": b"", "more_body": False})
        sent = []

        async def release():
            calls.append(1)

        async def view(request):
            events = Events([None])
            references.append(weakref.ref(events))
            return response_api()(events, aclose=release)

        async def send(message):
            sent.append(message)

        global urlpatterns
        urlpatterns = [path("events", view)]
        with override_settings(
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=["testserver"],
            MIDDLEWARE=[__name__ + ".SyncMiddleware"] if mixed else [],
        ):
            await async_application(ASGIHandler())(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/events",
                    "query_string": b"",
                    "headers": [(b"host", b"testserver")],
                },
                received.get,
                send,
            )
        await asyncio.sleep(0)
        gc.collect()
        assert calls == [1]
        assert sent[0]["status"] == 200
        assert sent[1]["body"] == b": heartbeat\n\n"
        assert sent[-1] == {"type": "http.response.body"}
        assert references[0]() is None, (
            "completed response retained by live request task"
        )

    run_in_scope(run)


def test_wrapper_non_http_passthrough_and_scope_refuses_late_registration():
    from dj_hyperview.realtime import realtime_asgi

    async def run():
        calls = []
        closed_context = None

        async def app(scope, receive, send):
            nonlocal closed_context
            import contextvars

            calls.append(scope["type"])
            closed_context = contextvars.copy_context()

        wrapped = realtime_asgi(app)
        await wrapped({"type": "lifespan"}, None, None)
        await wrapped({"type": "http"}, None, None)
        assert calls == ["lifespan", "http"]
        with pytest.raises(RuntimeError, match="active realtime ASGI scope"):
            closed_context.run(response_api(), Events(), aclose=lambda: None)
        with pytest.raises(RuntimeError, match="active realtime ASGI scope"):
            response_api()(Events(), aclose=lambda: None)

    asyncio.run(run())


def test_scope_double_cancel_still_finishes_owned_cleanup():
    from dj_hyperview.realtime import realtime_asgi

    async def run():
        closing = asyncio.Event()
        proceed = asyncio.Event()
        calls = []

        async def release():
            calls.append(1)
            closing.set()
            await proceed.wait()

        async def app(scope, receive, send):
            response_api()(Events(), aclose=release)
            await asyncio.Event().wait()

        task = asyncio.create_task(realtime_asgi(app)({"type": "http"}, None, None))
        await asyncio.sleep(0)
        task.cancel()
        await closing.wait()
        task.cancel()
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == [1]

    asyncio.run(run())


def test_iterator_without_aclose_and_repeated_read_after_close():
    async def run():
        class BareEvents:
            def __aiter__(self):
                return self

            async def __anext__(self):
                return None

        calls = []
        released = asyncio.Event()

        async def release():
            calls.append(1)
            released.set()

        response = response_api()(BareEvents(), aclose=release)
        response.close()
        await asyncio.wait_for(released.wait(), 1)
        with pytest.raises(StopAsyncIteration):
            await anext(response.streaming_content)
        assert calls == [1]
        with pytest.raises(TypeError):
            response_api()(BareEvents(), aclose=None)

    run_in_scope(run)
