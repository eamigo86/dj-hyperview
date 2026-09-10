"""Finite per-process publisher: bounded callers, not preemptible OS DNS."""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from ._clients import _sync_client
from ._config import _Config

_LOGGER = logging.getLogger("dj_hyperview.realtime")
_TIMEOUT = 2.0


@dataclass
class _Job:
    config: _Config = field(repr=False)
    channels: tuple[str, ...] = field(repr=False)
    payload: bytes = field(repr=False)
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    success: bool = False


def _send(
    config: _Config, channels: tuple[str, ...], payload: bytes, deadline: float
) -> None:
    client = _sync_client(config)
    try:
        with client.pipeline(transaction=False) as pipeline:
            for channel in channels:
                pipeline.publish(channel, payload)
            if time.monotonic() >= deadline:
                raise TimeoutError
            # One pipeline, but socket timeout is per operation, NOT a total IO bound.
            pipeline.execute()
    finally:
        client.close()


class _Dispatcher:
    def __init__(self) -> None:
        self.queue: queue.Queue[_Job | None] = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="djhv-realtime-publisher", daemon=True
        )
        self.thread.start()

    def submit(self, config, channels, payload, *, deadline=None) -> bool:
        deadline = time.monotonic() + _TIMEOUT if deadline is None else deadline
        if self.stopped.is_set() or time.monotonic() >= deadline:
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

    def _run(self) -> None:
        while not self.stopped.is_set():
            job = self.queue.get()
            if job is None:
                return
            try:
                if time.monotonic() < job.deadline:
                    _send(job.config, job.channels, job.payload, job.deadline)
                    job.success = True
            except Exception:
                _LOGGER.warning("realtime_publish_failed")
            finally:
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
        if _dispatcher is None:
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
