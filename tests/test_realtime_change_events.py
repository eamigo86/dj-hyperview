"""Closed v2 hints retain v1 compatibility and immutable commit capture."""

import copy
import importlib
import json

import pytest
from django.db import transaction

from dj_hyperview.realtime import RedisBroker
from dj_hyperview.realtime._events import _encode_payload, encode_event


def change():
    return {
        "event": "invalidate",
        "data": {
            "version": 2,
            "resources": ["tasks"],
            "mutation_id": "a" * 32 + "00000001",
            "entities": {
                "epoch": "b" * 16,
                "items": [{"resource": "tasks", "key": "c" * 64}],
            },
        },
    }


def test_capability_is_public_and_immutable():
    api = importlib.import_module("dj_hyperview.realtime")
    assert api.INVALIDATION_VERSIONS == (1, 2)
    assert "INVALIDATION_VERSIONS" in api.__all__


@pytest.mark.parametrize("unknown", [False, True])
def test_v2_payload_and_frame_preserve_only_closed_metadata(unknown):
    event = change()
    if unknown:
        event["data"].update(mutation_id=None, entities=None)
    assert json.loads(_encode_payload(event)) == event
    frame = encode_event(event)
    assert frame.startswith(b"event: invalidate\ndata: ")
    assert json.loads(frame.split(b"data: ", 1)[1]) == event["data"]


@pytest.mark.parametrize("kind", ["invalidate", "resync", "auth-required"])
def test_v1_wire_remains_exact(kind):
    data = {"version": 1}
    if kind == "invalidate":
        data["resources"] = ["tasks"]
    event = {"event": kind, "data": data}
    assert json.loads(_encode_payload(event)) == event
    assert json.loads(encode_event(event).split(b"data: ", 1)[1]) == data


@pytest.mark.parametrize(
    "mutation",
    ["a" * 32, "a" * 39, "a" * 41, "A" * 40, "g" * 40, 1, False, {}, "a" * 40 + "\n"],
)
def test_invalid_mutation_never_schedules_or_reflects_content(mutation, monkeypatch):
    event = change()
    event["data"]["mutation_id"] = mutation
    calls = []
    monkeypatch.setattr(transaction, "on_commit", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="^Invalid realtime event$"):
        RedisBroker("redis://localhost/0", "test-app").publish_after_commit(
            event, ["private.owner"], using="default"
        )
    assert calls == []


@pytest.mark.parametrize(
    "entities",
    [
        {},
        [],
        {"epoch": "b" * 16, "items": []},
        {"epoch": "B" * 16, "items": [{"resource": "tasks", "key": "c" * 64}]},
        {"epoch": "b" * 15, "items": [{"resource": "tasks", "key": "c" * 64}]},
        {"epoch": "b" * 16, "items": [{"resource": "ui", "key": "c" * 64}]},
        {"epoch": "b" * 16, "items": [{"resource": "tasks", "key": "C" * 64}]},
        {"epoch": "b" * 16, "items": [{"resource": "tasks", "key": "c" * 63}]},
        {
            "epoch": "b" * 16,
            "items": [{"resource": "tasks", "key": "c" * 64, "owner": "secret"}],
        },
        {"epoch": "b" * 16, "items": [{"resource": "tasks", "key": "c" * 64}] * 2},
        {
            "epoch": "b" * 16,
            "items": [{"resource": "tasks", "key": f"{n:064x}"} for n in range(33)],
        },
        {"epoch": "b" * 16, "items": "secret"},
        {"epoch": "b" * 16, "items": [None]},
        {
            "epoch": "b" * 16,
            "items": [{"resource": "tasks", "key": "c" * 64}],
            "extra": True,
        },
    ],
)
def test_entity_metadata_is_bounded_closed_and_resource_scoped(entities):
    event = change()
    event["data"]["entities"] = entities
    with pytest.raises(ValueError, match="^Invalid realtime event$"):
        _encode_payload(event)


def test_32_entities_fit_but_four_kib_limit_still_applies():
    event = change()
    event["data"]["entities"]["items"] = [
        {"resource": "tasks", "key": f"{n:064x}"} for n in range(32)
    ]
    assert len(_encode_payload(event)) < 4096
    resource = "r" * 64
    event["data"]["resources"] = [resource]
    for item in event["data"]["entities"]["items"]:
        item["resource"] = resource
    for encode in (_encode_payload, encode_event):
        with pytest.raises(ValueError, match="^Invalid realtime event$"):
            encode(event)


@pytest.mark.parametrize("kind", ["resync", "auth-required"])
def test_control_events_do_not_silently_upgrade_to_v2(kind):
    with pytest.raises(ValueError, match="^Invalid realtime event$"):
        encode_event({"event": kind, "data": {"version": 2}})


@pytest.mark.parametrize("edit", ["missing", "extra", "v1-extra", "bool-version"])
def test_version_schema_never_implicitly_accepts_extensions(edit):
    event = change()
    if edit == "missing":
        del event["data"]["mutation_id"]
    elif edit == "extra":
        event["data"]["actor"] = "private"
    elif edit == "v1-extra":
        event["data"]["version"] = 1
    else:
        event["data"]["version"] = True
    with pytest.raises(ValueError, match="^Invalid realtime event$"):
        encode_event(event)


@pytest.mark.django_db(transaction=True, databases=["default", "replica"])
def test_v2_nested_values_are_captured_before_alias_commit_and_rollback(monkeypatch):
    module = importlib.import_module("dj_hyperview.realtime._broker")
    calls = []
    monkeypatch.setattr(module, "_dispatch", lambda *args: calls.append(args))
    broker = RedisBroker("redis://localhost/0", "test-app")
    event = change()
    expected = copy.deepcopy(event)
    with transaction.atomic(using="default"):
        with transaction.atomic(using="replica"):
            broker.publish_after_commit(event, ["private.owner"], using="replica")
            event["data"]["entities"]["items"][0]["key"] = "d" * 64
            event["data"]["entities"]["epoch"] = "e" * 16
            event["data"]["mutation_id"] = None
            assert not calls
        assert len(calls) == 1
    assert json.loads(calls[0][2]) == expected
    with transaction.atomic(using="replica"):
        broker.publish_after_commit(change(), ["private.owner"], using="replica")
        transaction.set_rollback(True, using="replica")
    assert len(calls) == 1
