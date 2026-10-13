"""Finite per-process publisher: bounded callers, not preemptible OS DNS."""

import contextlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ._clients import _sync_client
from ._config import _Config

_LOGGER = logging.getLogger("dj_hyperview.realtime")
_TIMEOUT = 2.0
# A pooled connection idle for longer may have been dropped by the server or a
# middlebox, and the first command on it would fail without a retry.
_IDLE = 30.0
# A connection under steady traffic is never idle, so it is also replaced after this
# age, which makes the next one resolve the host name again.
_MAX_AGE = 300.0
# Consecutive failures that open the breaker, and how long it then fails fast.
_FAILURES = 3
_COOLDOWN = 5.0


@dataclass
class _Job:
    config: _Config = field(repr=False)
    channels: tuple[str, ...] = field(repr=False)
    payload: bytes = field(repr=False)
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    success: bool = False


@dataclass
class _Target:
    """Worker-owned client and circuit breaker state for one configuration.

    Only the worker thread touches the client, its age, its last use and the failure
    count. The open_until deadline is one float written by the worker and read by
    callers.
    """

    client: Any = None
    born: float = 0.0
    used: float = 0.0
    failures: int = 0
    open_until: float = 0.0


def _discard(target: _Target) -> None:
    client, target.client = target.client, None
    if client is not None:
        with contextlib.suppress(Exception):
            client.close()


def _send(
    target: _Target,
    config: _Config,
    channels: tuple[str, ...],
    payload: bytes,
    deadline: float,
) -> None:
    now = time.monotonic()
    if target.client is not None and (
        now - target.used > _IDLE or now - target.born > _MAX_AGE
    ):
        _discard(target)
    try:
        if target.client is None:
            target.client = _sync_client(config)
            target.born = now
        with target.client.pipeline(transaction=False) as pipeline:
            for channel in channels:
                pipeline.publish(channel, payload)
            if time.monotonic() >= deadline:
                raise TimeoutError
            # One pipeline, but socket timeout is per operation, NOT a total IO bound.
            pipeline.execute()
    except BaseException:
        _discard(target)
        target.failures += 1
        if target.failures >= _FAILURES:
            target.open_until = time.monotonic() + _COOLDOWN
            # Once per window: the callers that the open breaker turns away are silent.
            _LOGGER.warning("realtime_publish_circuit_open")
        raise
    target.failures = 0
    target.used = time.monotonic()


class _Dispatcher:
    def __init__(self) -> None:
        self.queue: queue.Queue[_Job | None] = queue.Queue(maxsize=32)
        self.targets: dict[_Config, _Target] = {}
        self.stopped = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="djhv-realtime-publisher", daemon=True
        )
        self.thread.start()

    def submit(self, config, channels, payload, *, deadline=None) -> bool:
        deadline = time.monotonic() + _TIMEOUT if deadline is None else deadline
        if self.stopped.is_set() or time.monotonic() >= deadline:
            return False
        if self._open(config):
            return False
        job = _Job(config, channels, payload, deadline)
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            _LOGGER.warning("realtime_publish_queue_full")
            return False
        if not job.done.wait(max(0, deadline - time.monotonic())):
            _LOGGER.warning("realtime_publish_timeout")
            return False
        return job.success

    def _open(self, config: _Config) -> bool:
        target = self.targets.get(config)
        return target is not None and time.monotonic() < target.open_until

    def _run(self) -> None:
        try:
            while not self.stopped.is_set():
                job = self.queue.get()
                if job is None:
                    break
                try:
                    if time.monotonic() < job.deadline and not self._open(job.config):
                        target = self.targets.setdefault(job.config, _Target())
                        _send(
                            target, job.config, job.channels, job.payload, job.deadline
                        )
                        job.success = True
                except Exception as error:
                    # The type name is code-defined; the message may carry a URL.
                    _LOGGER.warning(
                        "realtime_publish_failed error_type=%s", type(error).__name__
                    )
                finally:
                    job.done.set()
        finally:
            # Closed here, not in close(), so the worker is the only user of a client.
            # A BaseException ends the worker, and every client still goes with it.
            for target in self.targets.values():
                _discard(target)
            # Nothing will serve the callers still queued, and they would wait out
            # their deadlines. Their jobs end unsent, as a timed-out job does.
            self.stopped.set()
            while True:
                try:
                    job = self.queue.get_nowait()
                except queue.Empty:
                    break
                if job is not None:
                    job.done.set()

    def close(self) -> None:
        self.stopped.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=_TIMEOUT)


_dispatcher: _Dispatcher | None = None
_lock = threading.Lock()
_pid = os.getpid()


def _after_fork() -> None:
    global _dispatcher, _lock, _pid
    _dispatcher = None
    _lock = threading.Lock()
    _pid = os.getpid()


def _get_dispatcher() -> _Dispatcher:
    global _dispatcher
    if _pid != os.getpid():
        _after_fork()
    with _lock:
        # A BaseException ends the worker thread, and a dead one would leave every
        # later commit waiting for its full timeout.
        if _dispatcher is None or not _dispatcher.thread.is_alive():
            _dispatcher = _Dispatcher()
        return _dispatcher


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def dispatch(config: _Config, channels: tuple[str, ...], payload: bytes) -> None:
    """Wait at most the caller budget for a finite, best-effort hint job.

    Args:
        config: Immutable validated configuration.
        channels: Captured and prefixed logical topics.
        payload: Captured closed JSON envelope bytes.
    """
    deadline = time.monotonic() + _TIMEOUT
    _get_dispatcher().submit(config, channels, payload, deadline=deadline)
