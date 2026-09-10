"""Opt-in real Redis transport proof, isolated by fresh namespaces, never flush."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from django.db import transaction

pytestmark = pytest.mark.skipif(
    os.environ.get("DJHV_TEST_REDIS") != "1",
    reason="Redis compatibility is explicitly opt-in",
)
EVENT = {"event": "invalidate", "data": {"version": 1, "resources": ["tasks"]}}
RESYNC = {"event": "resync", "data": {"version": 1}}


def new_broker(suffix=""):
    from dj_hyperview.realtime import RedisBroker

    return RedisBroker(os.environ["DJHV_REDIS_URL"], "test-" + uuid4().hex + suffix)


@pytest.mark.django_db(transaction=True)
def test_real_redis_commit_alias_payload_and_namespace_isolation():
    async def run():
        broker, other = new_broker(), new_broker()
        subscription = await broker.subscribe(["private.a", "ui"])
        foreign = await other.subscribe(["private.a", "ui"])
        try:
            assert await anext(subscription) == await anext(foreign) == RESYNC

            def committed():
                with transaction.atomic():
                    broker.publish_after_commit(
                        EVENT, ["private.a", "ui"], using="default"
                    )

            await asyncio.to_thread(committed)
            assert await asyncio.wait_for(anext(subscription), 1) == EVENT
            assert await asyncio.wait_for(anext(subscription), 1) == EVENT
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(foreign), 0.03)

            def rolled_back():
                with transaction.atomic():
                    broker.publish_after_commit(EVENT, ["private.a"], using="default")
                    transaction.set_rollback(True)

            await asyncio.to_thread(rolled_back)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(subscription), 0.03)
        finally:
            await subscription.aclose()
            await foreign.aclose()

    try:
        asyncio.run(run())
    finally:
        from dj_hyperview.realtime import _publisher

        _publisher._get_dispatcher().close()
        _publisher._after_fork()


def test_real_redis_idle_beyond_two_seconds_and_heartbeat_cancellation():
    from dj_hyperview.realtime._clients import _async_client

    async def run():
        broker = new_broker()
        subscription = await broker.subscribe(["a"])
        client = _async_client(broker._config)
        try:
            assert await anext(subscription) == RESYNC
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(subscription), 2.2)
            await client.publish(broker._config.namespace + ":a", json.dumps(EVENT))
            assert await asyncio.wait_for(anext(subscription), 1) == EVENT
        finally:
            await subscription.aclose()
            await client.aclose()

    asyncio.run(run())


def test_real_retry_zero_control_and_configured_empty_errors_disable_failure_hook():
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff
    from redis.exceptions import ConnectionError

    from dj_hyperview.realtime._clients import _async_client, _sync_client

    async def run():
        broker = new_broker()
        client = _async_client(broker._config)
        failed = []

        async def attempt():
            raise ConnectionError("deliberate closed test transport")

        async def on_failure(*args):
            failed.append(1)

        try:
            with pytest.raises(ConnectionError):
                await Retry(NoBackoff(), 0).call_with_retry(attempt, on_failure)
            assert failed == [1]  # retries=0 alone DOES invoke failure/reconnect hook
            failed.clear()
            retry = client.connection_pool.connection_kwargs["retry"]
            with pytest.raises(ConnectionError):
                await retry.call_with_retry(attempt, on_failure)
            assert failed == []
        finally:
            await client.aclose()
        sync = _sync_client(broker._config)
        try:
            retry = sync.connection_pool.connection_kwargs["retry"]
            with pytest.raises(ConnectionError):
                retry.call_with_retry(
                    lambda: (_ for _ in ()).throw(ConnectionError()),
                    lambda *a: failed.append(1),
                )
            assert failed == []
        finally:
            sync.close()

    asyncio.run(run())


def test_real_pubsub_disconnect_does_not_run_reconnect_callback():
    from redis.exceptions import ConnectionError

    from dj_hyperview.realtime._clients import _async_client

    class Observer:
        def __init__(self):
            self.calls = 0

        async def connected(self, connection):
            self.calls += 1

    async def run():
        broker = new_broker()
        client = _async_client(broker._config)
        control = _async_client(broker._config)
        pubsub = client.pubsub()
        observer = Observer()
        try:
            # Obtain only OUR transport ID before entering PubSub mode.
            await pubsub.execute_command("CLIENT", "ID")
            own_id = await pubsub.parse_response()
            assert type(own_id) is int
            await pubsub.subscribe(broker._config.namespace + ":a")
            ack = await pubsub.get_message(
                ignore_subscribe_messages=False, timeout=None
            )
            assert ack["type"] == "subscribe"
            pubsub.connection.register_connect_callback(observer.connected)
            assert await control.client_kill_filter(_id=own_id) == 1
            with pytest.raises(ConnectionError):
                await pubsub.get_message(ignore_subscribe_messages=False, timeout=None)
            assert observer.calls == 0
        finally:
            await pubsub.aclose()
            await client.aclose()
            await control.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "url", ["unix:///tmp/djhv-not-opened.sock?db=0", "rediss://redis.example/0"]
)
def test_real_lazy_connection_constructors_accept_bounds_without_io(url):
    from dj_hyperview.realtime import RedisBroker
    from dj_hyperview.realtime._clients import _async_client, _sync_client

    async def run():
        config = RedisBroker(url, "test-construction")._config
        sync = _sync_client(config)
        asynchronous = _async_client(config)
        sync_connection = sync.connection_pool.make_connection()
        async_connection = asynchronous.connection_pool.make_connection()
        try:
            assert sync_connection.socket_connect_timeout == 2
            assert async_connection.socket_connect_timeout == 2
            assert sync_connection.socket_timeout == 2
            assert async_connection.socket_timeout is None
            if url.startswith("rediss:"):
                for client in (sync, asynchronous):
                    assert (
                        client.connection_pool.connection_kwargs["ssl_check_hostname"]
                        is True
                    )
                    assert (
                        client.connection_pool.connection_kwargs["ssl_cert_reqs"]
                        == "required"
                    )
        finally:
            sync_connection.disconnect()
            await async_connection.disconnect()
            sync.close()
            await asynchronous.aclose()

    asyncio.run(run())
