"""One finite process worker bounds commit callers, not resolver execution."""

import importlib
import threading
import time


def publisher():
    return importlib.import_module("dj_hyperview.realtime._publisher")


def test_one_worker_bounds_actual_caller_wait_and_does_not_retry(monkeypatch, caplog):
    module = publisher()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def send(*args):
        calls.append(threading.get_ident())
        entered.set()
        release.wait(5)

    monkeypatch.setattr(module, "_send", send)
    dispatcher = module._Dispatcher()
    try:
        started = time.monotonic()
        assert dispatcher.submit(None, ("topic",), b"payload") is False
        elapsed = time.monotonic() - started
        assert entered.is_set() and 1.8 <= elapsed < 2.5
        assert len(calls) == 1 and dispatcher.thread.is_alive()
        assert "payload" not in caplog.text and "topic" not in caplog.text
        assert "realtime_publish_timeout" in caplog.text
    finally:
        release.set()
        dispatcher.close()
    assert not dispatcher.thread.is_alive()


def test_queue32_rejects_overflow_and_expired_queued_job_never_sends(
    monkeypatch, caplog
):
    module = publisher()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def send(*args):
        calls.append(1)
        entered.set()
        release.wait(2)

    monkeypatch.setattr(module, "_send", send)
    dispatcher = module._Dispatcher()
    first = module._Job(None, ("topic",), b"x", time.monotonic() + 2)
    dispatcher.queue.put_nowait(first)
    assert entered.wait(1)
    expired = [
        module._Job(None, ("topic",), b"x", time.monotonic() - 1) for _ in range(32)
    ]
    try:
        for job in expired:
            dispatcher.queue.put_nowait(job)
        started = time.monotonic()
        assert dispatcher.submit(None, ("topic",), b"x") is False
        assert time.monotonic() - started < 0.2
        assert dispatcher.queue.qsize() == 32
        release.set()
        assert expired[-1].done.wait(1)
        assert calls == [1]
        assert "realtime_publish_queue_full" in caplog.text
    finally:
        release.set()
        dispatcher.close()


def test_worker_failure_is_contained_and_next_hint_progresses(monkeypatch, caplog):
    module = publisher()
    calls = []

    def send(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("redis://secret/private-topic")

    monkeypatch.setattr(module, "_send", send)
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(None, ("topic",), b"x") is False
        assert dispatcher.submit(None, ("topic",), b"x") is True
        assert calls == [1, 1]
        assert "secret" not in caplog.text
    finally:
        dispatcher.close()


def test_dispatcher_singleton_and_child_reset_do_not_reuse_parent_worker(monkeypatch):
    module = publisher()
    module._after_fork()
    first = module._get_dispatcher()
    try:
        assert module._get_dispatcher() is first
        module._after_fork()  # same child-reset operation, no OS process for this unit
        second = module._get_dispatcher()
        try:
            assert second is not first and second.thread is not first.thread
        finally:
            second.close()
    finally:
        first.close()
        module._after_fork()


def test_pipeline_checks_expiry_and_closes_client_without_sending(monkeypatch):
    import pytest

    module = publisher()
    calls = []

    class Client:
        def pipeline(self, *, transaction):
            assert transaction is False
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def publish(self, channel, payload):
            calls.append("queued")

        def execute(self):
            calls.append("sent")

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(module, "_sync_client", lambda _: Client())
    with pytest.raises(TimeoutError):
        module._send(None, ("a", "b"), b"x", time.monotonic() - 1)
    assert calls == ["queued", "queued", "closed"]


def test_stopped_dispatcher_and_changed_pid_do_not_reuse_worker(monkeypatch):
    module = publisher()
    first = module._get_dispatcher()
    first.close()
    assert first.submit(None, ("topic",), b"x") is False
    monkeypatch.setattr(module, "_pid", -1)
    second = module._get_dispatcher()
    try:
        assert first is not second
        assert (
            second.submit(None, ("topic",), b"x", deadline=time.monotonic() - 1)
            is False
        )
    finally:
        second.close()
        module._after_fork()
