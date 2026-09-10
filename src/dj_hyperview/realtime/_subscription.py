"""ACK-gated connection ownership and bounded, cancellation-safe hint queues."""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Protocol

from ._broker import RealtimeUnavailable
from ._clients import _async_client
from ._events import _encode_payload

_LOGGER = logging.getLogger("dj_hyperview.realtime")
_SETUP_TIMEOUT = 2.0
_CLOSE_TIMEOUT = 2.0
_RESYNC = b'{"event":"resync","data":{"version":1}}'
_END = object()


class Subscription(Protocol):
    """An acknowledged, single-loop stream with explicit idempotent ownership."""

    def __aiter__(self) -> "Subscription":
        """Return this stream.

        Returns:
            The acknowledged subscription iterator.
        """
        ...

    async def __anext__(self) -> Mapping[str, object]:
        """Wait for a hint; cancelling this wait does not close the subscription.

        Returns:
            A closed version-one envelope.

        Raises:
            StopAsyncIteration: If the subscription is closed or disconnected.
        """
        ...

    async def aclose(self) -> None:
        """Close owned Redis resources once on their creating event loop."""
        ...


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid realtime event")
        result[key] = value
    return result


def _payload(raw):
    if not isinstance(raw, bytes) or len(raw) > 4096:
        raise ValueError("Invalid realtime event")
    return _encode_payload(json.loads(raw, object_pairs_hook=_pairs))


async def _close_clients(pubsub, client):
    async def close():
        try:
            if pubsub is not None:
                await pubsub.aclose()
        finally:
            if client is not None:
                await client.aclose()

    async def bounded_close():
        task = asyncio.create_task(close())
        done, pending = await asyncio.wait([task], timeout=_CLOSE_TIMEOUT)
        if pending:
            task.cancel()
            _LOGGER.warning("realtime_subscription_cleanup_timeout")
            await asyncio.sleep(0)
        elif not task.cancelled() and task.exception() is not None:
            _LOGGER.warning("realtime_subscription_cleanup_failed")

    # Keep the same cleanup budget even when setup is cancelled more than once.
    cleanup = asyncio.create_task(bounded_close())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


class _Subscription:
    def __init__(self, pubsub, client, channels):
        self._pubsub = pubsub
        self._client = client
        self._channels = channels
        self._queue = asyncio.Queue(maxsize=32)
        self._queue.put_nowait(_RESYNC)
        self._resync_pending = True
        self._closing = None
        self._closed = False
        self._pump_task = asyncio.create_task(self._pump())

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed:
            raise StopAsyncIteration
        value = await self._queue.get()
        if value is _END:
            raise StopAsyncIteration
        if value == _RESYNC:
            self._resync_pending = False
        return json.loads(value)

    def _drain(self):
        while not self._queue.empty():
            self._queue.get_nowait()

    def _put(self, payload):
        if self._resync_pending:
            return
        if self._queue.full():
            self._drain()
            self._queue.put_nowait(_RESYNC)
            self._resync_pending = True
        else:
            self._queue.put_nowait(payload)

    async def _pump(self):
        try:
            while True:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=None
                )
                if message is None:
                    continue
                if (
                    message["type"] != "message"
                    or message["channel"] not in self._channels
                ):
                    raise ValueError
                self._put(_payload(message["data"]))
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.warning("realtime_subscription_failed")
            # No retry/reconnect; a fresh subscription must acknowledge and resync.
            self._begin_close()

    def _begin_close(self):
        if self._closing is None:
            self._closed = True
            self._drain()
            self._queue.put_nowait(_END)
            self._closing = asyncio.create_task(self._finish())

    async def _finish(self):
        if not self._pump_task.done():
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        await _close_clients(self._pubsub, self._client)

    async def aclose(self):
        self._begin_close()
        await asyncio.shield(self._closing)


async def _subscribe(config, channels) -> Subscription:
    client = pubsub = None
    expected = frozenset(channel.encode("ascii") for channel in channels)
    acknowledged = set()
    try:
        async with asyncio.timeout(_SETUP_TIMEOUT):
            client = _async_client(config)
            pubsub = client.pubsub()
            await pubsub.subscribe(*channels)
            while acknowledged != expected:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=None
                )
                if message is None:
                    continue
                kind, channel = message["type"], message["channel"]
                if channel not in expected:
                    raise ValueError
                if kind == "subscribe":
                    if (
                        channel in acknowledged
                        or type(message["data"]) is not int
                        or message["data"] != len(acknowledged) + 1
                    ):
                        raise ValueError
                    acknowledged.add(channel)
                elif kind != "message":
                    raise ValueError
                # Data interleaved before every ACK is covered by initial resync.
        return _Subscription(pubsub, client, expected)
    except BaseException as error:
        await _close_clients(pubsub, client)
        if isinstance(error, asyncio.CancelledError):
            raise
        raise RealtimeUnavailable("Realtime unavailable") from None
