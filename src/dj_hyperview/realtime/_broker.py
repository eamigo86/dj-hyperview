"""Lazy public broker facade and immutable after-commit publication inputs."""

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from django.db import transaction

from ._config import _channels, _config
from ._events import _encode_payload

if TYPE_CHECKING:
    from ._subscription import Subscription

_LOGGER = logging.getLogger("dj_hyperview.realtime")


class RealtimeUnavailable(RuntimeError):
    """The optional broker could not establish or retain its owned transport."""


def _dispatch(config, channels, payload):
    from ._publisher import dispatch

    dispatch(config, channels, payload)


class RedisBroker:
    """Lazy, namespace-isolated Redis hints without durable-delivery guarantees."""

    def __init__(self, url: str, namespace: str) -> None:
        """Capture validated server configuration without importing Redis or I/O.

        Args:
            url: Redis, TLS Redis or Unix URL; only a sole db query is accepted.
            namespace: Lowercase app/environment prefix, at most 64 ASCII characters.

        Raises:
            ValueError: If configuration is invalid or overrides transport safeguards.
        """
        self._config = _config(url, namespace)

    def publish_after_commit(
        self, event: Mapping[str, object], topics: Iterable[str], *, using: str
    ) -> None:
        """Capture one bounded hint and publish only after the selected commit.

        Args:
            event: A closed supported event envelope, never application document data.
            topics: Up to 32 server-selected logical topics, captured before commit.
            using: Explicit database alias whose commit owns publication.

        Raises:
            ValueError: If the alias, envelope or topics are invalid before scheduling.
        """
        if not isinstance(using, str) or not using:
            raise ValueError("Invalid database alias")
        channels = _channels(self._config, topics)
        payload = _encode_payload(event)

        def publish() -> None:
            try:
                _dispatch(self._config, channels, payload)
            except Exception:
                _LOGGER.warning("realtime_publish_failed")

        transaction.on_commit(publish, using=using)

    async def subscribe(self, topics: Iterable[str]) -> "Subscription":
        """Acquire an owned stream only after every selected Redis SUBSCRIBE ACK.

        Args:
            topics: At most 32 server-selected logical topics.

        Returns:
            An acknowledged subscription whose first item is one resync envelope.

        Raises:
            ValueError: If topics are invalid before transport admission.
            RealtimeUnavailable: If setup fails or its two-second budget expires.
        """
        from ._subscription import _subscribe

        channels = _channels(self._config, topics)
        return await _subscribe(self._config, channels)
