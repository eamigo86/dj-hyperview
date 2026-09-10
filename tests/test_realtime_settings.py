"""Central realtime configuration reuses the broker's closed security contract."""

import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest
from django.core.checks import ERROR
from django.test import override_settings

from dj_hyperview import HyperviewConfigurationError
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.realtime import RedisBroker


@pytest.mark.parametrize("raw", [{}, {"REALTIME": None}])
def test_realtime_defaults_disabled(raw):
    with override_settings(HYPERVIEW=raw):
        assert get_settings().realtime is None
        assert not [e for e in check_hyperview_settings() if e.level >= ERROR]


@pytest.mark.parametrize(
    "url",
    [
        "redis://localhost:6379/0",
        "rediss://user:secret@redis.example/2",
        "redis://[::1]:6379?db=1",
        "unix:///tmp/owned.sock?db=0",
    ],
)
def test_realtime_settings_share_valid_broker_configuration(url):
    raw = MappingProxyType({"REDIS_URL": url, "NAMESPACE": "app-dev"})
    with override_settings(HYPERVIEW={"REALTIME": raw}):
        config = get_settings().realtime
        assert config.redis_url == url
        assert config.namespace == "app-dev"
        broker = RedisBroker(config.redis_url, namespace=config.namespace)
        assert broker._config.url == config.redis_url
        assert not [e for e in check_hyperview_settings() if e.level >= ERROR]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        "redis://secret-host/0",
        True,
        0,
        {"REDIS_URL": "redis://localhost/0"},
        {"NAMESPACE": "app"},
        {"ALIAS": "default"},
        {"REDIS_URL": "redis://localhost/0", "NAMESPACE": "app", "ALIAS": "default"},
        {"REDIS_URL": None, "NAMESPACE": "app"},
        {"REDIS_URL": "redis://localhost/0", "NAMESPACE": None},
        {"redis_url": "redis://localhost/0", "namespace": "app"},
    ],
)
def test_realtime_invalid_shape_has_one_redacted_system_check(raw):
    with override_settings(HYPERVIEW={"REALTIME": raw}):
        errors = [e for e in check_hyperview_settings() if e.level >= ERROR]
        assert len(errors) == 1
        assert errors[0].id == "dj_hyperview.E022"
        assert errors[0].obj == "settings.HYPERVIEW"
        assert "REALTIME" in errors[0].msg
        assert "REDIS_URL" in errors[0].msg and "NAMESPACE" in errors[0].msg
        assert "secret-host" not in str(errors[0])
        with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E022"):
            get_settings()


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
        "unix://host/path",
        "unix:relative",
        "redis://user:secret@host/not-a-db",
    ],
)
def test_realtime_url_security_matches_direct_broker(url):
    with pytest.raises(ValueError, match="Invalid Redis configuration"):
        RedisBroker(url, "app")
    with override_settings(
        HYPERVIEW={"REALTIME": {"REDIS_URL": url, "NAMESPACE": "app"}}
    ):
        with pytest.raises(HyperviewConfigurationError, match="E022") as error:
            get_settings()
        assert url not in str(error.value)
        assert "secret" not in repr(error.value)


@pytest.mark.parametrize("namespace", ["", "x:y", "x.y", "*", "x" * 65, "é"])
def test_realtime_namespace_security_matches_direct_broker(namespace):
    with pytest.raises(ValueError):
        RedisBroker("redis://localhost/0", namespace)
    with override_settings(
        HYPERVIEW={
            "REALTIME": {
                "REDIS_URL": "redis://localhost/0",
                "NAMESPACE": namespace,
            }
        }
    ):
        with pytest.raises(HyperviewConfigurationError, match="E022"):
            get_settings()


def test_realtime_public_type_snapshot_and_override_restore():
    from dj_hyperview.conf import RealtimeSettings

    raw = {"REDIS_URL": "rediss://user:secret@localhost/0", "NAMESPACE": "first"}
    with override_settings(HYPERVIEW={"REALTIME": raw}):
        first = get_settings()
        assert isinstance(first.realtime, RealtimeSettings)
        assert first.realtime == RealtimeSettings(raw["REDIS_URL"], "first")
        raw["NAMESPACE"] = "mutated"
        assert get_settings() is first
        assert first.realtime.namespace == "first"
        assert "secret" not in repr(first)
        assert "rediss" not in repr(first.realtime)
        with pytest.raises(FrozenInstanceError):
            first.realtime.namespace = "forbidden"
        assert not hasattr(first.realtime, "__dict__")
        with override_settings(HYPERVIEW={"REALTIME": None}):
            assert get_settings().realtime is None
        assert get_settings().realtime.namespace == "mutated"
    with override_settings(HYPERVIEW={}):
        assert get_settings().realtime is None
    with pytest.raises(ValueError, match="Invalid Redis configuration"):
        RealtimeSettings("rediss://user:secret@host?ssl_cert_reqs=none", "app")


@pytest.mark.parametrize("enabled", [False, True])
def test_fresh_process_checks_never_import_optional_redis_or_start_network(
    enabled, monkeypatch
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    source_root = Path(__file__).resolve().parents[1] / "src"
    assert (source_root / "dj_hyperview" / "__init__.py").is_file()
    code = """
import builtins
import socket
import sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "redis" or name.startswith("redis."):
        raise AssertionError("optional Redis import")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
def no_network(*args, **kwargs):
    raise AssertionError("network access during configuration")
socket.socket.connect = no_network
socket.getaddrinfo = no_network
from django.conf import settings
settings.configure(INSTALLED_APPS=["dj_hyperview"], HYPERVIEW=RAW)
import django
django.setup()
from dj_hyperview.conf import get_settings
from dj_hyperview.checks import check_hyperview_settings
config = get_settings()
assert (config.realtime is not None) == ENABLED
assert not [e for e in check_hyperview_settings() if e.level >= 40]
assert not any(n == "redis" or n.startswith("redis.") for n in sys.modules)
assert "dj_hyperview.realtime" not in sys.modules
"""
    raw = (
        {"REALTIME": {"REDIS_URL": "redis://localhost/0", "NAMESPACE": "app"}}
        if enabled
        else {}
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code.replace("RAW", repr(raw)).replace("ENABLED", repr(enabled)),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(source_root),
        },
    )
    assert result.returncode == 0, result.stderr
