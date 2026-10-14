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


def test_idle_connection_is_replaced_before_use(monkeypatch, clock):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a",), b"x") is True
        clock.now += module._IDLE + 1
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


def test_a_client_that_raises_a_base_exception_when_closed_does_not_leak_the_rest(
    monkeypatch,
):
    module = publisher()

    class Stop(BaseException):
        pass

    ended_by = []
    monkeypatch.setattr(threading, "excepthook", lambda args: ended_by.append(args))
    made = fake_clients(monkeypatch, module)
    first = module._Config("redis://127.0.0.1/0", "ns")
    second = module._Config("redis://127.0.0.1/1", "ns")
    dispatcher = module._Dispatcher()

    def close(self):
        self.closed = True
        if self is made[0]:
            raise Stop

    try:
        assert dispatcher.submit(first, ("a",), b"x") is True
        assert dispatcher.submit(second, ("a",), b"x") is True
        monkeypatch.setattr(made[0].__class__, "close", close)
    finally:
        dispatcher.close()
        dispatcher.thread.join(2)

    assert not dispatcher.thread.is_alive()
    assert [client.closed for client in made] == [True, True]
    # The BaseException still ends the worker; it is not swallowed on the way.
    assert [args.exc_type for args in ended_by] == [Stop]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_callers_queued_behind_a_worker_that_dies_return_at_once(monkeypatch):
    module = publisher()

    class Stop(BaseException):
        pass

    made = fake_clients(monkeypatch, module)
    config = module._Config("redis://127.0.0.1/0", "ns")
    entered = threading.Event()
    release = threading.Event()
    outcomes = []

    def die_when_released(self):
        entered.set()
        release.wait(5)
        raise Stop

    def caller():
        # A deadline far beyond the test: only the dying worker can end the wait.
        outcomes.append(
            dispatcher.submit(config, ("a",), b"x", deadline=time.monotonic() + 60)
        )

    dispatcher = module._Dispatcher()
    callers = [threading.Thread(target=caller, daemon=True) for _ in range(3)]
    try:
        assert dispatcher.submit(config, ("a",), b"x") is True
        monkeypatch.setattr(made[0].__class__, "execute", die_when_released)
        callers[0].start()
        assert entered.wait(2)
        callers[1].start()
        callers[2].start()
        while dispatcher.queue.qsize() < 2:
            time.sleep(0.001)
        release.set()
        for thread in callers:
            thread.join(2)
        assert not any(thread.is_alive() for thread in callers)
        assert outcomes == [False, False, False]
    finally:
        release.set()
        dispatcher.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_callers_are_released_even_when_closing_a_client_ends_the_cleanup(
    monkeypatch,
):
    """A BaseException from close() must not skip releasing the queued callers."""
    module = publisher()

    class Stop(BaseException):
        pass

    made = fake_clients(monkeypatch, module)
    healthy = module._Config("redis://127.0.0.1/0", "ns")
    dying = module._Config("redis://127.0.0.1/1", "ns")
    entered = threading.Event()
    release = threading.Event()
    outcomes = []

    def close(self):
        self.closed = True
        if self is made[0]:
            raise Stop

    def die_when_released(self):
        entered.set()
        release.wait(5)
        raise Stop

    def caller(config):
        # A deadline far beyond the test: only the dying worker can end the wait.
        outcomes.append(
            dispatcher.submit(config, ("a",), b"x", deadline=time.monotonic() + 60)
        )

    dispatcher = module._Dispatcher()
    callers = [
        threading.Thread(target=caller, args=(config,), daemon=True)
        for config in (dying, healthy, healthy)
    ]
    try:
        # The first client stays open, and closing it is what raises.
        assert dispatcher.submit(healthy, ("a",), b"x") is True
        monkeypatch.setattr(made[0].__class__, "close", close)
        monkeypatch.setattr(made[0].__class__, "execute", die_when_released)
        callers[0].start()
        assert entered.wait(2)
        callers[1].start()
        callers[2].start()
        while dispatcher.queue.qsize() < 2:
            time.sleep(0.001)
        release.set()
        for thread in callers:
            thread.join(2)
        assert not any(thread.is_alive() for thread in callers)
        assert outcomes == [False, False, False]
        assert dispatcher.stopped.is_set()
    finally:
        release.set()
        dispatcher.close()


def test_a_dispatcher_that_stops_while_a_job_is_queued_returns_at_once(monkeypatch):
    """The worker may stop between the check on entry and the put: nobody serves it."""
    module = publisher()
    dispatcher = module._Dispatcher()
    try:
        # The put that races with the stop, which the worker never gets to serve.
        monkeypatch.setattr(
            dispatcher.queue, "put_nowait", lambda job: dispatcher.stopped.set()
        )
        started = time.monotonic()

        assert dispatcher.submit(None, ("a",), b"x", deadline=started + 10) is False
        assert time.monotonic() - started < 5
    finally:
        monkeypatch.undo()
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


def configs(module, count):
    return [module._Config("redis://127.0.0.1/0", f"ns{i}") for i in range(count)]


def wait_until(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        time.sleep(0.005)
    return condition()


@pytest.mark.parametrize("age", ["_IDLE", "_MAX_AGE"])
def test_a_job_closes_the_clients_of_configurations_never_used_again(
    monkeypatch, clock, age
):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    first, *others = configs(module, 6)
    dispatcher = module._Dispatcher()
    try:
        for config in (first, *others):
            assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(made) == 6 and not any(client.closed for client in made)

        clock.now += getattr(module, age) + 1
        assert dispatcher.submit(first, ("a",), b"x") is True

        # Only the configuration in use again was asked for, yet none is left open
        # but its replacement, and the others do not stay in the table either.
        assert [client.closed for client in made] == [True] * 6 + [False]
        assert list(dispatcher.targets) == [first]
    finally:
        dispatcher.close()


def test_a_busy_configuration_keeps_its_client_while_an_idle_one_goes(
    monkeypatch, clock
):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    busy, idle = configs(module, 2)
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(busy, ("a",), b"x") is True
        assert dispatcher.submit(idle, ("a",), b"x") is True
        for _ in range(2):
            clock.now += module._IDLE * 0.6
            assert dispatcher.submit(busy, ("a",), b"x") is True

        assert [client.config for client in made] == [busy, idle]
        assert not made[0].closed and made[1].closed
        assert list(dispatcher.targets) == [busy]
    finally:
        dispatcher.close()


def test_an_idle_worker_closes_expired_clients_without_any_job(monkeypatch, clock):
    module = publisher()
    made = fake_clients(monkeypatch, module)
    # The worker wakes up this often, in real time, while nothing is submitted.
    monkeypatch.setattr(module, "_IDLE", 0.01)
    expire = module._expire

    def slow_expire(target, now):
        expire(target, now)
        # The sweep forgets a target only after it has closed the client.
        time.sleep(0.05)

    monkeypatch.setattr(module, "_expire", slow_expire)
    dispatcher = module._Dispatcher()
    try:
        for config in configs(module, 3):
            assert dispatcher.submit(config, ("a",), b"x") is True
        assert not any(client.closed for client in made)

        clock.now += 1

        assert wait_until(lambda: all(client.closed for client in made))
        assert wait_until(lambda: dispatcher.targets == {})
        assert dispatcher.thread.is_alive()
    finally:
        dispatcher.close()


def test_the_worker_waits_on_the_queue_with_a_timeout_instead_of_spinning(
    monkeypatch,
):
    module = publisher()
    waits = []

    class Spy(module.queue.Queue):
        def get(self, block=True, timeout=None):
            waits.append((block, timeout))
            return super().get(block, timeout)

    monkeypatch.setattr(module.queue, "Queue", Spy)
    fake_clients(monkeypatch, module)
    (config,) = configs(module, 1)
    dispatcher = module._Dispatcher()
    try:
        assert dispatcher.submit(config, ("a",), b"x") is True
        assert wait_until(lambda: len(waits) >= 2)
    finally:
        dispatcher.close()

    # Blocking for up to _IDLE each time: neither forever nor a busy loop.
    assert waits[:2] == [(True, module._IDLE)] * 2


def test_a_sweep_keeps_the_breaker_state_of_a_failing_configuration(monkeypatch, clock):
    module = publisher()
    made = fake_clients(
        monkeypatch, module, lambda config: config.url.endswith("/down")
    )
    down = module._Config("redis://127.0.0.1/down", "ns")
    up = module._Config("redis://127.0.0.1/up", "ns")
    dispatcher = module._Dispatcher()

    def connections():
        return [client.config for client in made].count(down)

    try:
        for _ in range(3):
            assert dispatcher.submit(down, ("a",), b"x") is False
        # A job for another configuration sweeps while the breaker is open...
        assert dispatcher.submit(up, ("a",), b"x") is True
        assert dispatcher.submit(down, ("a",), b"x") is False
        assert connections() == 3
        # ...and again once it may probe, which still reopens it on one failure.
        clock.now += module._COOLDOWN + 1
        assert dispatcher.submit(up, ("a",), b"x") is True
        assert dispatcher.submit(down, ("a",), b"x") is False
        assert dispatcher.submit(down, ("a",), b"x") is False
        assert connections() == 4
    finally:
        dispatcher.close()


def count_expiries(monkeypatch, module):
    """Record each target that a sweep or a send looks at."""
    walked = []
    expire = module._expire

    def counting(target, now):
        walked.append(target)
        expire(target, now)

    monkeypatch.setattr(module, "_expire", counting)
    return walked


def test_a_failed_attempt_counts_as_the_last_use_of_a_target(monkeypatch, clock):
    module = publisher()
    fake_clients(monkeypatch, module, lambda config: True)
    target = module._Target()

    with pytest.raises(ConnectionError):
        module._send(target, None, ("a",), b"x", clock.now + 60)

    assert target.client is None and target.failures == 1
    assert target.used == clock.now


def test_a_burst_of_failing_configurations_costs_work_linear_in_their_number(
    monkeypatch, clock
):
    module = publisher()
    fake_clients(monkeypatch, module, lambda config: True)
    walked = count_expiries(monkeypatch, module)
    dispatcher = module._Dispatcher()
    try:
        for config in configs(module, 100):
            assert dispatcher.submit(config, ("a",), b"x") is False
    finally:
        dispatcher.close()

    # One look from each send. A sweep per job added 0 + 1 + ... + 99 more.
    assert len(walked) <= 2 * 100


def test_failed_configurations_are_forgotten_after_the_idle_period(monkeypatch, clock):
    module = publisher()
    fake_clients(monkeypatch, module, lambda config: config.namespace != "ns0")
    good, *failing = configs(module, 50)
    dispatcher = module._Dispatcher()
    try:
        for config in failing:
            assert dispatcher.submit(config, ("a",), b"x") is False
        clock.now += module._IDLE - 1
        dispatcher._sweep()
        assert len(dispatcher.targets) == 49

        clock.now += module._IDLE + 1
        assert dispatcher.submit(good, ("a",), b"x") is True
        assert list(dispatcher.targets) == [good]

        # A job a month later walks only the configurations that are still live.
        walked = count_expiries(monkeypatch, module)
        clock.now += 30 * 24 * 3600
        assert dispatcher.submit(good, ("a",), b"x") is True
        assert len(walked) == 2 and list(dispatcher.targets) == [good]
    finally:
        dispatcher.close()


def test_jobs_sweep_at_most_once_per_idle_period(monkeypatch, clock):
    module = publisher()
    fake_clients(monkeypatch, module)
    sweeps = []
    sweep = module._Dispatcher._sweep
    monkeypatch.setattr(
        module._Dispatcher, "_sweep", lambda self: (sweeps.append(1), sweep(self))[1]
    )
    dispatcher = module._Dispatcher()
    try:
        for config in configs(module, 20):
            assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(sweeps) <= 1
        clock.now += module._IDLE
        for config in configs(module, 20):
            assert dispatcher.submit(config, ("a",), b"x") is True
        assert len(sweeps) <= 2
    finally:
        dispatcher.close()


def test_failures_older_than_the_idle_period_do_not_count_toward_the_breaker(
    monkeypatch, clock
):
    module = publisher()
    made = fake_clients(monkeypatch, module, lambda config: True)
    (config,) = configs(module, 1)
    dispatcher = module._Dispatcher()
    try:
        for _ in range(2):
            assert dispatcher.submit(config, ("a",), b"x") is False
        clock.now += module._IDLE + 1
        # Two more failures: the first two were forgotten, so the breaker is not open.
        for _ in range(2):
            assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 4 and not dispatcher._open(config)
        assert dispatcher.submit(config, ("a",), b"x") is False
        assert len(made) == 5 and dispatcher._open(config)
    finally:
        dispatcher.close()


def test_a_sustained_outage_costs_one_attempt_per_cooldown(monkeypatch, clock):
    module = publisher()
    made = fake_clients(monkeypatch, module, lambda config: True)
    (config,) = configs(module, 1)
    dispatcher = module._Dispatcher()
    try:
        for _ in range(600):
            clock.now += 1
            assert dispatcher.submit(config, ("a",), b"x") is False
        # Three failures open the breaker, then one probe in each window of five
        # seconds, however many sweeps ran in ten minutes.
        assert len(made) == 3 + len(range(8, 601, 5))
        assert config in dispatcher.targets
    finally:
        dispatcher.close()


def test_a_sweep_never_forgets_a_target_whose_breaker_is_open(monkeypatch, clock):
    module = publisher()
    monkeypatch.setattr(module, "_COOLDOWN", 3 * module._IDLE)
    fake_clients(monkeypatch, module, lambda config: True)
    (config,) = configs(module, 1)
    dispatcher = module._Dispatcher()
    try:
        for _ in range(3):
            assert dispatcher.submit(config, ("a",), b"x") is False
        # Long unused, but callers still read open_until to turn their hints away.
        clock.now += 2 * module._IDLE
        dispatcher._sweep()
        assert config in dispatcher.targets and dispatcher._open(config)

        clock.now += module._IDLE
        dispatcher._sweep()
        assert config not in dispatcher.targets
    finally:
        dispatcher.close()
