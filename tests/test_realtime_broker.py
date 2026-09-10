"""Bounded broker configuration and after-commit capture, without a service."""

import importlib
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from django.db import transaction


def broker_api():
    from dj_hyperview.realtime import RedisBroker

    return RedisBroker


@pytest.mark.parametrize(
    "url",
    [
        "redis://localhost:6379/0",
        "rediss://user:secret@redis.example/2",
        "redis://[::1]:6379?db=1",
        "unix:///tmp/owned.sock?db=0",
    ],
)
def test_config_accepts_dns_tls_unix_and_never_connects(url, monkeypatch):
    imports = []
    original = __import__

    def guarded(name, *args, **kwargs):
        if name == "redis" or name.startswith("redis."):
            imports.append(name)
            raise AssertionError("eager Redis import")
        return original(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    broker = broker_api()(url, "test-app")
    assert imports == []
    assert "secret" not in repr(broker)


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "redis://",
        "redis://host:0",
        "redis://host:65536",
        "redis://host/0#fragment",
        "redis://host/hello",
        "redis://host/-1",
        "redis://host/0?db=1",
        "redis://host?db=1&db=1",
        "redis://host?db=01",
        "redis://host?socket_timeout=0",
        "redis://host?retry_on_timeout=True",
        "rediss://host?ssl_cert_reqs=none",
        "rediss://host?ssl_check_hostname=False",
        "redis://host?unknown=x",
        " redis://host",
        "redis://host/0\n",
    ],
)
def test_config_rejects_overrides_and_malformed_url_without_reflection(url):
    with pytest.raises(ValueError) as caught:
        broker_api()(url, "test-app")
    assert str(caught.value) == "Invalid Redis configuration"


@pytest.mark.parametrize("namespace", ["", "x:y", "x.y", "*", "x" * 65, "é"])
def test_namespace_cannot_collide_or_select_patterns(namespace):
    with pytest.raises(ValueError, match="Invalid Redis configuration"):
        broker_api()("redis://localhost/0", namespace)


@pytest.mark.django_db(transaction=True, databases=["default", "replica"])
def test_capture_event_topics_alias_before_single_on_commit(monkeypatch):
    broker = broker_api()("redis://localhost/0", "test-app")
    module = importlib.import_module("dj_hyperview.realtime._broker")
    calls = []
    monkeypatch.setattr(module, "_dispatch", lambda *args: calls.append(args))
    event = {"event": "invalidate", "data": {"version": 1, "resources": ["tasks"]}}
    topics = ["private.A", "ui.default", "private.A"]
    with transaction.atomic(using="default"):
        with transaction.atomic(using="replica"):
            broker.publish_after_commit(event, topics, using="replica")
            event["data"]["resources"].append("ui")
            topics[:] = ["foreign"]
            assert calls == []
        assert len(calls) == 1  # selected alias committed; default still open
    config, channels, payload = calls[0]
    assert channels == ("test-app:private.A", "test-app:ui.default")
    assert json.loads(payload) == {
        "event": "invalidate",
        "data": {"version": 1, "resources": ["tasks"]},
    }
    with pytest.raises(FrozenInstanceError):
        config.url = "redis://foreign"


@pytest.mark.django_db(transaction=True)
def test_rollback_has_no_publication_and_failure_does_not_change_commit(
    monkeypatch, caplog
):
    broker = broker_api()("redis://localhost/0", "test-app")
    module = importlib.import_module("dj_hyperview.realtime._broker")
    calls = []

    def failed(*args):
        calls.append(1)
        raise RuntimeError("redis://credential/topic")

    monkeypatch.setattr(module, "_dispatch", failed)
    event = {"event": "resync", "data": {"version": 1}}
    with transaction.atomic():
        broker.publish_after_commit(event, ["topic"], using="default")
        transaction.set_rollback(True)
    assert calls == []
    with transaction.atomic():
        broker.publish_after_commit(event, ["topic"], using="default")
    assert calls == [1]
    assert "credential" not in caplog.text
    assert "realtime_publish_failed" in caplog.text


@pytest.mark.parametrize(
    "topics",
    [[], "topic", ["*"], ["bad topic"], ["x" * 129], [f"t{i}" for i in range(33)]],
)
def test_bad_topics_fail_before_scheduling(topics, monkeypatch):
    calls = []
    monkeypatch.setattr(transaction, "on_commit", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="Invalid realtime topics"):
        broker_api()("redis://localhost/0", "test-app").publish_after_commit(
            {"event": "resync", "data": {"version": 1}}, topics, using="default"
        )
    assert calls == []


def test_capture_is_finitely_bounded_and_alias_validated_before_iterable():
    seen = []

    def topics():
        while True:
            seen.append(1)
            yield "same"

    broker = broker_api()("redis://localhost/0", "test-app")
    with pytest.raises(ValueError, match="database alias"):
        broker.publish_after_commit({}, topics(), using="")
    assert not seen
    with pytest.raises(ValueError, match="Invalid realtime topics"):
        broker.publish_after_commit(
            {"event": "resync", "data": {"version": 1}}, topics(), using="default"
        )
    assert len(seen) == 33


def test_bad_envelope_never_schedules(monkeypatch):
    calls = []
    monkeypatch.setattr(transaction, "on_commit", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="Invalid realtime event"):
        broker_api()("redis://localhost/0", "test-app").publish_after_commit(
            {
                "event": "invalidate",
                "data": {"version": 1, "resources": ["https://secret"]},
            },
            ["topic"],
            using="default",
        )
    assert not calls


def test_fresh_process_import_and_construction_need_no_redis_or_worker(monkeypatch):
    import subprocess
    import sys

    monkeypatch.delenv("PYTHONPATH", raising=False)
    source_root = Path(__file__).resolve().parents[1] / "src"
    assert (source_root / "dj_hyperview" / "__init__.py").is_file()
    program = """
import builtins, threading
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name == "redis" or name.startswith("redis."):
        raise AssertionError("Redis import during startup")
    return original(name, *args, **kwargs)
builtins.__import__ = guard
from dj_hyperview.realtime import RedisBroker
RedisBroker("redis://localhost/0", "test-app")
assert threading.active_count() == 1
print("lazy-import-ok")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(source_root)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy-import-ok"
