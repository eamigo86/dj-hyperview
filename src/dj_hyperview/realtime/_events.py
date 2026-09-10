"""Validate closed, bounded transport envelopes before serialization."""

import json
import re
from collections.abc import Mapping

INVALIDATION_VERSIONS = (1, 2)
_HEX = re.compile(r"[0-9a-f]+\Z", re.ASCII)
_RESOURCE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)


def _capture_event(event: Mapping[str, object]) -> dict[str, object]:
    valid = isinstance(event, Mapping) and set(event) == {"event", "data"}
    kind = event.get("event") if valid else None
    data = event.get("data") if valid else None
    if kind not in ("invalidate", "resync", "auth-required") or not isinstance(
        data, Mapping
    ):
        raise ValueError("Invalid realtime event")
    version = data.get("version")
    expected = {"version", "resources"} if kind == "invalidate" else {"version"}
    if kind == "invalidate" and version == 2:
        expected |= {"mutation_id", "entities"}
    versions = INVALIDATION_VERSIONS if kind == "invalidate" else (1,)
    if set(data) != expected or type(version) is not int or version not in versions:
        raise ValueError("Invalid realtime event")
    captured = {"version": version}
    if kind == "invalidate":
        resources = data["resources"]
        if not isinstance(resources, (list, tuple)) or not 1 <= len(resources) <= 32:
            raise ValueError("Invalid realtime event")
        if any(
            not isinstance(item, str) or not _RESOURCE.fullmatch(item)
            for item in resources
        ):
            raise ValueError("Invalid realtime event")
        if len(set(resources)) != len(resources):
            raise ValueError("Invalid realtime event")
        captured["resources"] = list(resources)
        if version == 2:
            mutation = data["mutation_id"]
            if mutation is not None and not _hex(mutation, 40):
                raise ValueError("Invalid realtime event")
            captured["mutation_id"] = mutation
            captured["entities"] = _entities(data["entities"], resources)
    return {"event": kind, "data": captured}


def _hex(value: object, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and bool(_HEX.fullmatch(value))


def _entities(value: object, resources: list | tuple) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"epoch", "items"}:
        raise ValueError("Invalid realtime event")
    items = value["items"]
    if (
        not _hex(value["epoch"], 16)
        or not isinstance(items, (list, tuple))
        or not 1 <= len(items) <= 32
    ):
        raise ValueError("Invalid realtime event")
    captured = []
    seen = set()
    for item in items:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"resource", "key"}
            or not isinstance(item["resource"], str)
            or item["resource"] not in resources
            or not _hex(item["key"], 64)
            or (item["resource"], item["key"]) in seen
        ):
            raise ValueError("Invalid realtime event")
        seen.add((item["resource"], item["key"]))
        captured.append({"resource": item["resource"], "key": item["key"]})
    return {"epoch": value["epoch"], "items": captured}


def _encode_payload(event: Mapping[str, object]) -> bytes:
    payload = json.dumps(
        _capture_event(event), separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    if len(payload) > 4096:
        raise ValueError("Invalid realtime event")
    return payload


def encode_event(event: Mapping[str, object] | None) -> bytes:
    """Serialize one validated frame without reflecting rejected content.

    Args:
        event: A closed logical event, or None for a heartbeat.

    Returns:
        One complete UTF-8 SSE frame.

    Raises:
        ValueError: If the event violates the closed, bounded wire contract.
    """
    if event is None:
        return b": heartbeat\n\n"
    captured = _capture_event(event)
    payload = json.dumps(
        captured["data"], separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    if len(payload) > 4096:
        raise ValueError("Invalid realtime event")
    return (
        b"event: " + captured["event"].encode("ascii") + b"\ndata: " + payload + b"\n\n"
    )
