"""ACK admission, queue bounds, heartbeat cancellation and explicit ownership."""

import asyncio
import importlib
import json
from typing import Any, Never

import pytest

from dj_hyperview.realtime import RedisBroker, Subscription

RESYNC = {"event": "resync", "data": {"version": 1}}
INVALIDATE = {"event": "invalidate", "data": {"version": 1, "resources": ["tasks"]}}


class PubSub:
    """A fake pub/sub connection that reads the messages the test queues."""

    def __init__(self) -> None:
        """Create the message queue and the counters of the connection."""
        self.messages = asyncio.Queue()
        self.subscribed = asyncio.Event()
        self.topics = None
        self.closed = 0
        self.reads = 0

    async def subscribe(self, *topics: object) -> None:
        """Record the topics and mark the connection as subscribed.

        Args:
            *topics: Topics the client subscribes to.
        """
        self.topics = topics
        self.subscribed.set()

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float | None
    ) -> Any:
        """Return the next queued message, or raise a queued exception.

        Args:
            ignore_subscribe_messages: Whether subscribe acknowledgements are skipped.
            timeout: Seconds to wait for a message, or None.

        Returns:
            The next message that was queued.
        """
        assert ignore_subscribe_messages is False and timeout is None
        self.reads += 1
        value = await self.messages.get()
        self.messages.task_done()
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        """Count the calls that close the connection."""
        self.closed += 1


class Client:
    """A fake Redis client that hands out one fake pub/sub connection.

    Args:
        pubsub: Fake pub/sub connection the client returns.
    """

    def __init__(self, pubsub: PubSub) -> None:
        """Keep the pub/sub connection that the client hands out."""
        self.subscription = pubsub
        self.closed = 0

    def pubsub(self) -> PubSub:
        """Return the fake pub/sub connection.

        Returns:
            The fake pub/sub connection of the client.
        """
        return self.subscription

    async def aclose(self) -> None:
        """Count the calls that close the client."""
        self.closed += 1


def port(monkeypatch: pytest.MonkeyPatch) -> tuple[RedisBroker, PubSub, Client]:
    """Patch the broker with a fake client and return the broker with its fakes.

    Args:
        monkeypatch: Patches the code under test for this test.

    Returns:
        The broker, the fake pub/sub connection and the fake client.
    """
    module = importlib.import_module("dj_hyperview.realtime._subscription")
    pubsub = PubSub()
    client = Client(pubsub)
    monkeypatch.setattr(module, "_async_client", lambda _: client)
    return RedisBroker("redis://localhost/0", "test"), pubsub, client


async def ack(pubsub: PubSub, channel: str, count: int) -> None:
    """Queue a subscribe acknowledgement for a channel.

    Args:
        pubsub: Fake pub/sub connection of the test.
        channel: Redis channel of the message.
        count: Number of subscriptions the acknowledgement reports.
    """
    await pubsub.messages.put(
        {"type": "subscribe", "channel": channel.encode(), "data": count}
    )


async def message(
    pubsub: PubSub, payload: bytes | None = None, channel: str = "test:a"
) -> None:
    """Queue a message for a channel.

    Args:
        pubsub: Fake pub/sub connection of the test.
        payload: Message body, or None for a valid invalidate event.
        channel: Redis channel of the message.
    """
    await pubsub.messages.put(
        {
            "type": "message",
            "channel": channel.encode(),
            "data": json.dumps(INVALIDATE).encode() if payload is None else payload,
        }
    )


async def open_one(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Subscription, PubSub, Client]:
    """Open one subscription and answer its acknowledgement.

    Args:
        monkeypatch: Patches the code under test for this test.

    Returns:
        The open subscription, the fake pub/sub connection and the fake client.
    """
    broker, pubsub, client = port(monkeypatch)
    task = asyncio.create_task(broker.subscribe(["a"]))
    await pubsub.subscribed.wait()
    await ack(pubsub, "test:a", 1)
    return await task, pubsub, client


def test_waits_every_real_ack_and_coalesces_interleaved_data_before_initial_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscribing waits for every real ACK and coalesces data before the first resync.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        broker, pubsub, client = port(monkeypatch)
        task = asyncio.create_task(broker.subscribe(["a", "b"]))
        await pubsub.subscribed.wait()
        assert not task.done()
        await ack(pubsub, "test:a", 1)
        await message(pubsub)
        await pubsub.messages.join()
        assert not task.done()
        await ack(pubsub, "test:b", 2)
        subscription = await task
        assert await anext(subscription) == RESYNC
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(subscription), 0.01)
        await message(pubsub)
        assert await anext(subscription) == INVALIDATE
        await subscription.aclose()
        await subscription.aclose()
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


def test_ack_timeout_and_cancellation_close_owned_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACK timeout and cancellation close owned clients.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        from dj_hyperview.realtime import RealtimeUnavailable

        module = importlib.import_module("dj_hyperview.realtime._subscription")
        monkeypatch.setattr(module, "_SETUP_TIMEOUT", 0.02)
        broker, pubsub, client = port(monkeypatch)
        with pytest.raises(RealtimeUnavailable, match="Realtime unavailable"):
            await broker.subscribe(["a"])
        assert pubsub.closed == client.closed == 1
        broker, pubsub, client = port(monkeypatch)
        task = asyncio.create_task(broker.subscribe(["a"]))
        await pubsub.subscribed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize("signal", [SystemExit, KeyboardInterrupt])
def test_setup_closes_clients_but_never_converts_process_exit_signals(
    monkeypatch: pytest.MonkeyPatch, signal: type[BaseException]
) -> None:
    """Setup closes clients but never converts process exit signals.

    Args:
        monkeypatch: Patches the code under test for this test.
        signal: Process exit signal the setup raises.
    """

    async def run() -> None:
        broker, pubsub, client = port(monkeypatch)
        await pubsub.messages.put(signal())
        with pytest.raises(signal):
            await broker.subscribe(["a"])
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


def test_queue32_overflow_retains_one_resync_and_heartbeat_timeout_does_not_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queue of 32 keeps one resync on overflow, and a heartbeat timeout stays open.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        subscription, pubsub, client = await open_one(monkeypatch)
        assert await anext(subscription) == RESYNC
        for _ in range(40):
            await message(pubsub)
        await pubsub.messages.join()
        assert subscription._queue.qsize() == 1
        assert await anext(subscription) == RESYNC
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(subscription), 0.02)
        assert pubsub.closed == 0
        await message(pubsub)
        assert await anext(subscription) == INVALIDATE
        await subscription.aclose()
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        b"x" * 4097,
        b'{"event":"resync","event":"resync","data":{"version":1}}',
        b'{"event":"resync","data":{"version":true}}',
        b"\xff",
    ],
)
def test_malformed_redis_payload_closes_without_emitting_or_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed Redis payload closes without emitting or reconnect.

    Args:
        monkeypatch: Patches the code under test for this test.
        payload: Message body, or None for a valid invalidate event.
        caplog: Captures the log records of the subscription.
    """

    async def run() -> None:
        subscription, pubsub, client = await open_one(monkeypatch)
        assert await anext(subscription) == RESYNC
        await message(pubsub, payload)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(subscription), 1)
        await subscription.aclose()
        assert pubsub.closed == client.closed == 1
        assert "realtime_subscription_failed" in caplog.text

    asyncio.run(run())


def test_disconnect_closes_and_drops_queued_hints_without_reconnecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect closes and drops queued hints without reconnecting.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        subscription, pubsub, client = await open_one(monkeypatch)
        assert await anext(subscription) == RESYNC
        await message(pubsub)
        await pubsub.messages.put(ConnectionError("redis://secret"))
        await pubsub.messages.join()
        await asyncio.sleep(0)
        await subscription.aclose()
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)
        assert pubsub.closed == client.closed == 1
        reads = pubsub.reads
        await asyncio.sleep(0)
        assert reads == pubsub.reads

    asyncio.run(run())


def test_ack_for_foreign_channel_never_admits(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACK for foreign channel never admits.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        from dj_hyperview.realtime import RealtimeUnavailable

        broker, pubsub, client = port(monkeypatch)
        task = asyncio.create_task(broker.subscribe(["a"]))
        await pubsub.subscribed.wait()
        await ack(pubsub, "test:foreign", 1)
        with pytest.raises(RealtimeUnavailable):
            await task
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,count", [("subscribe", True), ("subscribe", 2), ("unsubscribe", 1)]
)
def test_wrong_ack_kind_or_count_refuses_admission(
    monkeypatch: pytest.MonkeyPatch, kind: str, count: int
) -> None:
    """Wrong ACK kind or count refuses admission.

    Args:
        monkeypatch: Patches the code under test for this test.
        kind: Type of the acknowledgement.
        count: Number of subscriptions the acknowledgement reports.
    """

    async def run() -> None:
        from dj_hyperview.realtime import RealtimeUnavailable

        broker, pubsub, client = port(monkeypatch)
        task = asyncio.create_task(broker.subscribe(["a"]))
        await pubsub.subscribed.wait()
        await pubsub.messages.put(None)
        await pubsub.messages.put({"type": kind, "channel": b"test:a", "data": count})
        with pytest.raises(RealtimeUnavailable):
            await task
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize("factory", ["client", "pubsub"])
def test_construction_failure_keeps_bounded_cleanup(
    monkeypatch: pytest.MonkeyPatch, factory: str
) -> None:
    """Construction failure keeps bounded cleanup.

    Args:
        monkeypatch: Patches the code under test for this test.
        factory: Name of the factory that fails.
    """

    async def run() -> None:
        from dj_hyperview.realtime import RealtimeUnavailable

        broker, pubsub, client = port(monkeypatch)
        module = importlib.import_module("dj_hyperview.realtime._subscription")

        def fail(*args: object) -> Never:
            raise RuntimeError("sensitive unavailable dependency")

        if factory == "client":
            monkeypatch.setattr(module, "_async_client", fail)
        else:
            monkeypatch.setattr(client, "pubsub", fail)
        with pytest.raises(RealtimeUnavailable, match="^Realtime unavailable$"):
            await broker.subscribe(["a"])
        assert client.closed == (factory == "pubsub")
        assert pubsub.closed == 0

    asyncio.run(run())


@pytest.mark.parametrize("timeout", [False, True])
def test_pubsub_cleanup_error_or_timeout_still_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pub/sub cleanup error or timeout still closes the client.

    Args:
        monkeypatch: Patches the code under test for this test.
        timeout: Seconds to wait for a message, or None.
        caplog: Captures the log records of the subscription.
    """

    async def run() -> None:
        subscription, pubsub, client = await open_one(monkeypatch)
        assert aiter(subscription) is subscription
        module = importlib.import_module("dj_hyperview.realtime._subscription")
        monkeypatch.setattr(module, "_CLOSE_TIMEOUT", 0.02)

        async def fail() -> Never:
            if timeout:
                await asyncio.Event().wait()
            raise RuntimeError("sensitive transport")

        monkeypatch.setattr(pubsub, "aclose", fail)
        await subscription.aclose()
        assert client.closed == 1
        assert "sensitive" not in caplog.text
        assert "realtime_subscription_cleanup_" in caplog.text

    asyncio.run(run())


def test_post_ack_unexpected_protocol_message_closes_pending_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected protocol message after the ACK closes the pending read.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        subscription, pubsub, client = await open_one(monkeypatch)
        assert await anext(subscription) == RESYNC
        waiting = asyncio.create_task(anext(subscription))
        await pubsub.messages.put(None)
        await pubsub.messages.put(
            {"type": "subscribe", "channel": b"test:a", "data": 1}
        )
        with pytest.raises(StopAsyncIteration):
            await waiting
        await subscription.aclose()
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())


def test_double_cancel_during_setup_waits_for_owned_client_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Double cancel during setup waits for owned client cleanup.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        broker, pubsub, client = port(monkeypatch)
        entered, allowed, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def close_pubsub() -> None:
            calls.append("pubsub")
            entered.set()
            await allowed.wait()

        async def close_client() -> None:
            calls.append("client")
            released.set()

        monkeypatch.setattr(pubsub, "aclose", close_pubsub)
        monkeypatch.setattr(client, "aclose", close_client)
        task = asyncio.create_task(broker.subscribe(["a"]))
        await asyncio.wait_for(pubsub.subscribed.wait(), 1)
        task.cancel()
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        try:
            # Loop turns rather than a time window: cleanup is blocked on allowed.
            for _ in range(50):
                await asyncio.sleep(0)
            returned_before_cleanup = task.done() and not released.is_set()
        finally:
            allowed.set()
            result = await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(released.wait(), 1)
        assert calls == ["pubsub", "client"]
        assert isinstance(result[0], asyncio.CancelledError)
        assert not returned_before_cleanup

    asyncio.run(run())


def test_repeated_setup_cancellation_does_not_renew_cleanup_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated setup cancellation does not renew cleanup budget.

    Args:
        monkeypatch: Patches the code under test for this test.
    """

    async def run() -> None:
        broker, pubsub, client = port(monkeypatch)
        module = importlib.import_module("dj_hyperview.realtime._subscription")
        monkeypatch.setattr(module, "_CLOSE_TIMEOUT", 0.03)
        entered = asyncio.Event()

        async def close_pubsub() -> None:
            pubsub.closed += 1
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(pubsub, "aclose", close_pubsub)
        task = asyncio.create_task(broker.subscribe(["a"]))
        await pubsub.subscribed.wait()
        task.cancel()
        await entered.wait()

        async def keep_cancelling() -> None:
            while not task.done():
                task.cancel()
                await asyncio.sleep(0.005)

        cancellations = asyncio.create_task(keep_cancelling())
        try:
            # The cleanup budget is 0.03 s; a renewed budget would never end, so this
            # generous limit only bounds the failing case.
            done, _ = await asyncio.wait({task}, timeout=5)
        finally:
            cancellations.cancel()
            await asyncio.gather(cancellations, return_exceptions=True)
            result = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), 1
            )
        assert done, "repeated cancellation renewed the cleanup deadline"
        assert isinstance(result[0], asyncio.CancelledError)
        assert pubsub.closed == client.closed == 1

    asyncio.run(run())
