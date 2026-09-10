"""Validate closed, bounded transport envelopes before serialization."""

import json
import re
from collections.abc import Mapping

_RESOURCE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)


def _capture_event(event: Mapping[str, object]) -> dict[str, object]:
    valid = isinstance(event, Mapping) and set(event) == {"event", "data"}
    kind = event.get("event") if valid else None
    data = event.get("data") if valid else None
    if kind not in ("invalidate", "resync", "auth-required") or not isinstance(
        data, Mapping
    ):
        raise ValueError("Invalid realtime event")
    expected = {"version", "resources"} if kind == "invalidate" else {"version"}
    if (
        set(data) != expected
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise ValueError("Invalid realtime event")
    captured = {"version": 1}
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
    return {"event": kind, "data": captured}


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
