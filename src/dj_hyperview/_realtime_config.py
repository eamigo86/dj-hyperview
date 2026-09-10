"""Pure shared Redis validation; safe before optional transport imports."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z", re.ASCII)
_DB = re.compile(r"0|[1-9][0-9]{0,8}\Z", re.ASCII)


def _validate_redis_config(url: str, namespace: str) -> None:
    try:
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 4096
            or any(ord(c) <= 32 for c in url)
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.fragment
            or not isinstance(namespace, str)
            or not _NAMESPACE.fullmatch(namespace)
        ):
            raise ValueError
        if parsed.scheme in ("redis", "rediss"):
            if not parsed.hostname or parsed.port == 0:
                raise ValueError
            if parsed.path not in ("", "/") and not _DB.fullmatch(
                parsed.path.removeprefix("/")
            ):
                raise ValueError
        elif parsed.scheme == "unix":
            if parsed.netloc or not parsed.path.startswith("/"):
                raise ValueError
        else:
            raise ValueError
        if parsed.query:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if len(pairs) != 1 or pairs[0][0] != "db" or not _DB.fullmatch(pairs[0][1]):
                raise ValueError
            if parsed.scheme != "unix" and parsed.path not in ("", "/"):
                raise ValueError

    except (ValueError, TypeError):
        raise ValueError("Invalid Redis configuration") from None


@dataclass(frozen=True, slots=True)
class RealtimeSettings:
    """Validated optional Redis configuration without connection ownership."""

    redis_url: str = field(repr=False)
    namespace: str

    def __post_init__(self) -> None:
        """Validate direct construction without importing Redis or exposing secrets.

        Raises:
            ValueError: If the URL or namespace violates transport safeguards.
        """
        _validate_redis_config(self.redis_url, self.namespace)


def _realtime_settings(raw: object) -> RealtimeSettings | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != {"REDIS_URL", "NAMESPACE"}:
        raise ValueError("Invalid realtime configuration")
    return RealtimeSettings(redis_url=raw["REDIS_URL"], namespace=raw["NAMESPACE"])
