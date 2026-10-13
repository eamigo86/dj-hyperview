"""One finite process worker bounds commit callers, not resolver execution."""

import importlib
import threading
import time

import pytest


def publisher():
    return importlib.import_module("dj_hyperview.realtime._publisher")


class FakeClock:
    """A monotonic clock that stands still until a test moves it."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(publisher(), "time", fake)
    return fake


def test_one_worker_bounds_actual_caller_wait_and_does_not_retry(
    monkeypatch, caplog, clock
):
    module = publisher()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    deadlines = []

    def send(target, config, channels, payload, deadline):
        calls.append(threading.get_ident())
        deadlines.append(deadline)
        entered.set()
        release.wait(5)

    monkeypatch.setattr(module, "_send", send)
    dispatcher = module._Dispatcher()
    try:
        with monkeypatch.context() as short:
            # Real wait kept short; the deadline itself is read off the fake clock.
            short.setattr(module, "_TIMEOUT", 0.05)
            assert dispatcher.submit(None, ("topic",), b"payload") is False
        # The caller gave up while the worker was still inside the send.
        assert entered.is_set() and not release.is_set()
        assert deadlines == [clock.now + 0.05]
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
        assert dispatcher.submit(None, ("topic",), b"x") is False
        assert dispatcher.queue.qsize() == 32
        release.set()
        assert expired[-1].done.wait(1)
        assert calls == [1]
        # Rejected on the spot: the caller did not wait out its deadline.
        assert "realtime_publish_queue_full" in caplog.text
        assert "realtime_publish_timeout" not in caplog.text
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
        assert "realtime_publish_failed error_type=RuntimeError" in caplog.text
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


def fake_clients(monkeypatch, module, fail=lambda config: False):
    """Replace redis-py with recording clients and return the constructed ones."""
    made = []

    class Client:
        def __init__(self, config):
            self.config = config
            self.calls = []
            self.closed = False
            made.append(self)

        def pipeline(self, *, transaction):
            assert transaction is False
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def publish(self, channel, payload):
            self.calls.append("queued")

        def execute(self):
            self.calls.append("sent")
            if fail(self.config):
                raise ConnectionError("redis://secret/private-topic")

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "_sync_client", Client)
    return made


def test_pipeline_checks_expiry_and_discards_client_without_sending(monkeypatch):
    import pytest

    module = publisher()
    made = fake_clients(monkeypatch, module)
    target = module._Target()
    with pytest.raises(TimeoutError):
        module._send(target, None, ("a", "b"), b"x", time.monotonic() - 1)
    assert made[0].calls == ["queued", "queued"]
    assert made[0].closed and target.client is None


def test_worker_reuses_one_pipelined_client_and_closes_it_on_stop(monkeypatch):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a", "b"), b"x") is True
        assert dispatcher.submit(config, ("c",), b"x") is True
        assert len(made) == 1 and not made[0].closed
        # Several channels, one round trip.
        assert made[0].calls == ["queued", "queued", "sent", "queued", "sent"]
    finally:
        dispatcher.close()
    assert made[0].closed


def test_idle_connection_is_replaced_before_use(monkeypatch):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    monkeypatch.setattr(module, "_IDLE", -1)
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(made) == 2 and made[0].closed and not made[1].closed
    finally:
        dispatcher.close()


def test_failure_discards_the_client_and_the_next_hint_reconnects(monkeypatch):
    module = publisher()
    outcomes = iter([True, False])
    made = fake_clients(monkeypatch, module, lambda config: next(outcomes))
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a",), b"x") is False
        assert made[0].closed
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(made) == 2 and not made[1].closed
    finally:
        dispatcher.close()


def test_forked_child_neither_inherits_nor_closes_the_parent_client(monkeypatch):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    module._after_fork()
    parent = module._get_dispatcher()
    try:
        assert parent.submit(config, ("a",), b"x") is True
        module._after_fork()  # same child-reset operation, no OS process for this unit
        child = module._get_dispatcher()
        try:
            assert child is not parent and child.targets == {}
            assert not made[0].closed
        finally:
            child.close()
    finally:
        parent.close()
        module._after_fork()


def test_breaker_opens_after_consecutive_failures_and_fails_fast(monkeypatch, caplog):
    module = publisher()
    made = fake_clients(monkeypatch, module, lambda config: True)
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        for _ in range(3):
            assert dispatcher.submit(config, ("private-topic",), b"x") is False
        assert len(made) == 3
        # Turned away before any client is built or any job queued.
        assert dispatcher.submit(config, ("private-topic",), b"payload") is False
        assert len(made) == 3
        assert "realtime_publish_circuit_open" in caplog.text
        assert "payload" not in caplog.text and "private-topic" not in caplog.text
        assert "secret" not in caplog.text
    finally:
        dispatcher.close()


def test_breaker_half_opens_after_cooldown_and_closes_on_success(monkeypatch, clock):
    module = publisher()
    down = [True]
    made = fake_clients(monkeypatch, module, lambda config: down[0])
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        for _ in range(3):
            assert dispatcher.submit(config, ("a",), b"x") is False
        clock.now += module._COOLDOWN + 1
        # One probe is let through; failing again reopens the breaker at once.
        assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 4
        assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 4
        clock.now += module._COOLDOWN + 1
        down[0] = False
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(made) == 5
    finally:
        dispatcher.close()


def test_breaker_state_is_per_configuration(monkeypatch):
    module = publisher()
    made = fake_clients(
        monkeypatch, module, lambda config: config.url.endswith("/down")
    )
    down = module._Config("redis://127.0.0.1/down", "ns")
    up = module._Config("redis://127.0.0.1/up", "ns")
    dispatcher = module._Dispatcher()
    try:
        for _ in range(3):
            assert dispatcher.submit(down, ("a",), b"x") is False
        assert dispatcher.submit(down, ("a",), b"x") is False
        assert [client.config for client in made].count(down) == 3
        assert dispatcher.submit(up, ("a",), b"x") is True
    finally:
        dispatcher.close()


def test_jobs_queued_before_the_breaker_opened_do_not_connect(monkeypatch):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        dispatcher.targets[config] = module._Target(open_until=time.monotonic() + 60)
        job = module._Job(config, ("a",), b"x", time.monotonic() + 2)
        dispatcher.queue.put_nowait(job)
        assert job.done.wait(1)
        assert job.success is False and made == []
    finally:
        dispatcher.close()


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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_base_exception_in_the_client_does_not_strand_later_hints(monkeypatch):
    """A gevent Timeout is a BaseException: it ends the worker, so a new one starts."""
    module = publisher()

    class Stop(BaseException):
        pass

    calls = []

    def send(*args):
        calls.append(1)
        if len(calls) == 1:
            raise Stop

    monkeypatch.setattr(module, "_send", send)
    module._after_fork()
    first = module._get_dispatcher()
    try:
        assert first.submit(None, ("topic",), b"x") is False
        first.thread.join(2)
        assert not first.thread.is_alive()
        second = module._get_dispatcher()
        try:
            assert second is not first and second.thread.is_alive()
            assert second.submit(None, ("topic",), b"x") is True
            assert module._get_dispatcher() is second
        finally:
            second.close()
    finally:
        first.close()
        module._after_fork()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_worker_ended_by_a_base_exception_still_closes_every_client(monkeypatch):
    module = publisher()

    class Stop(BaseException):
        pass

    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    other = module._Config("redis://127.0.0.1/1", "ns")
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a",), b"x") is True
        monkeypatch.setattr(made[0].__class__, "execute", raise_stop(Stop))
        assert dispatcher.submit(other, ("a",), b"x") is False
        dispatcher.thread.join(2)
        assert not dispatcher.thread.is_alive()
        assert all(client.closed for client in made) and len(made) == 2
    finally:
        dispatcher.close()


def raise_stop(error):
    def execute(self):
        raise error

    return execute


def test_circuit_open_is_logged_once_per_cooldown_window(monkeypatch, caplog, clock):
    module = publisher()
    made = fake_clients(monkeypatch, module, lambda config: True)
    config = module._Config("redis://:s3cr3t-pw@secret-host:6379/0", "ns")
    dispatcher = module._Dispatcher()

    def opened():
        return caplog.text.count("realtime_publish_circuit_open")

    try:
        for _ in range(3):
            assert dispatcher.submit(config, ("a",), b"x") is False
        for _ in range(50):
            assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 3 and opened() == 1
        clock.now += module._COOLDOWN + 1
        # The probe fails and opens a second window, which is logged once again.
        assert dispatcher.submit(config, ("a",), b"x") is False
        for _ in range(50):
            assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 4 and opened() == 2
        assert "s3cr3t" not in caplog.text and "secret-host" not in caplog.text
    finally:
        dispatcher.close()


def test_failures_must_be_consecutive_to_open_the_breaker(monkeypatch, caplog):
    module = publisher()
    outcomes = iter([True, True, False, True, True, False])
    made = fake_clients(monkeypatch, module, lambda config: next(outcomes))
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        results = [dispatcher.submit(config, ("a",), b"x") for _ in range(6)]
        assert results == [False, False, True, False, False, True]
        assert len(made) == 5
        assert "realtime_publish_circuit_open" not in caplog.text
    finally:
        dispatcher.close()


def test_a_client_that_cannot_be_built_counts_toward_the_breaker(monkeypatch, caplog):
    module = publisher()
    built = []

    def broken(config):
        built.append(config)
        raise ValueError("redis://:s3cr3t-pw@secret-host:6379/0")

    monkeypatch.setattr(module, "_sync_client", broken)
    config = module._Config("redis://:s3cr3t-pw@secret-host:6379/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        for _ in range(3):
            assert dispatcher.submit(config, ("a",), b"x") is False
        assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(built) == 3 and dispatcher.thread.is_alive()
        assert "realtime_publish_failed error_type=ValueError" in caplog.text
        assert "realtime_publish_circuit_open" in caplog.text
        assert "s3cr3t" not in caplog.text and "secret-host" not in caplog.text
    finally:
        dispatcher.close()


def test_a_connection_is_replaced_after_its_maximum_age_despite_traffic(monkeypatch):
    """A busy connection is never idle, yet DNS must be resolved again some day."""
    module = publisher()
    made = fake_clients(monkeypatch, module)
    target = module._Target()
    deadline = time.monotonic() + 60

    module._send(target, None, ("a",), b"x", deadline)
    module._send(target, None, ("a",), b"x", deadline)
    assert len(made) == 1 and not made[0].closed

    target.born -= module._MAX_AGE + 1
    module._send(target, None, ("a",), b"x", deadline)

    assert len(made) == 2 and made[0].closed and not made[1].closed
    module._send(target, None, ("a",), b"x", deadline)
    assert len(made) == 2
