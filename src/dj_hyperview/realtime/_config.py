"""Immutable transport configuration and finite, server-owned topic capture."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .._realtime_config import _validate_redis_config

_TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class _Config:
    url: str = field(repr=False)
    namespace: str


def _config(url: str, namespace: str) -> _Config:
    _validate_redis_config(url, namespace)
    return _Config(url, namespace)


def _channels(config: _Config, topics: Iterable[str]) -> tuple[str, ...]:
    if isinstance(topics, (str, bytes)):
        raise ValueError("Invalid realtime topics")
    captured = {}
    for count, topic in enumerate(topics, 1):
        if count > 32 or not isinstance(topic, str) or not _TOPIC.fullmatch(topic):
            raise ValueError("Invalid realtime topics")
        captured[f"{config.namespace}:{topic}"] = None
    if not captured:
        raise ValueError("Invalid realtime topics")
    return tuple(captured)
