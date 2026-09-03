"""Typed raw-template caching through Django's cache framework."""

import hashlib
import json
from dataclasses import asdict, dataclass

from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError

from .conf import get_settings
from .exceptions import SourceUnavailable
from .sources import ResolvedTemplate

_ABSENT = object()
_FIELDS = {"name", "content", "origin", "source", "revision"}
_MISS_FIELDS = {"version", "state"}
_TEMPLATE_FIELDS = {*_MISS_FIELDS, "template"}


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A present cache entry containing a template or an explicit miss."""

    template: ResolvedTemplate | None

    @property
    def is_miss(self) -> bool:
        return self.template is None


CACHE_MISS = CacheEntry(None)


def _validate_timeout(value: object, minimum: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Cache {label} must be an integer >= {minimum}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def template_cache_key(namespace: str, source: str, name: str, revision: str) -> str:
    """Return a backend-safe key for one raw template revision."""
    components = json.dumps(
        [namespace, source, name, revision],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"djhv:v1:{hashlib.sha256(components).hexdigest()}"


class TemplateCache:
    """Store serialized raw template results in a configured Django cache."""

    def __init__(
        self,
        namespace: str,
        *,
        alias: str = "default",
        ttl: int = 300,
        negative_ttl: int = 15,
    ) -> None:
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Cache namespace must be a non-empty string")
        _validate_timeout(ttl, 1, "TTL")
        _validate_timeout(negative_ttl, 0, "negative TTL")
        if not isinstance(alias, str) or not alias:
            raise SourceUnavailable("cache", "invalid alias")
        self.namespace = namespace
        self.alias = alias
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        try:
            self.backend = caches[alias]
        except InvalidCacheBackendError as error:
            raise SourceUnavailable(f"cache:{alias}", "unknown alias") from error

    @classmethod
    def from_settings(cls, namespace: str) -> "TemplateCache":
        config = get_settings().cache
        return cls(
            namespace,
            alias=config.alias,
            ttl=config.ttl,
            negative_ttl=config.negative_ttl,
        )

    def key(self, source: str, name: str, revision: str) -> str:
        return template_cache_key(self.namespace, source, name, revision)

    def get(self, source: str, name: str, revision: str) -> CacheEntry | None:
        payload = self.backend.get(self.key(source, name, revision), _ABSENT)
        if payload is _ABSENT:
            return None
        return self._decode(payload, source, name, revision)

    def set(self, template: ResolvedTemplate) -> None:
        payload = json.dumps(
            {"version": 1, "state": "template", "template": asdict(template)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.backend.set(
            self.key(template.source, template.name, template.revision),
            payload,
            timeout=self.ttl,
        )

    def set_miss(self, source: str, name: str, revision: str) -> None:
        payload = json.dumps({"version": 1, "state": "miss"}, separators=(",", ":"))
        self.backend.set(
            self.key(source, name, revision), payload, timeout=self.negative_ttl
        )

    def _decode(
        self, payload: object, source: str, name: str, revision: str
    ) -> CacheEntry:
        try:
            if not isinstance(payload, str):
                raise TypeError
            value = json.loads(payload, object_pairs_hook=_unique_object)
            if type(value.get("version")) is not int or value["version"] != 1:
                raise ValueError
            if value.get("state") == "miss":
                if set(value) != _MISS_FIELDS:
                    raise ValueError
                return CACHE_MISS
            if value.get("state") != "template" or set(value) != _TEMPLATE_FIELDS:
                raise ValueError
            template = value["template"]
            if (
                set(template) != _FIELDS
                or not all(isinstance(item, str) for item in template.values())
                or (template["source"], template["name"], template["revision"])
                != (source, name, revision)
            ):
                raise ValueError
            return CacheEntry(ResolvedTemplate(**template))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload") from error
