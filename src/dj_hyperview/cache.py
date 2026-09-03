"""Typed raw-template caching through Django's cache framework."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import BaseCache

from .conf import get_settings
from .exceptions import SourceUnavailable
from .sources import ResolvedTemplate

_ABSENT = object()
_FAILURE = object()
_BACKEND_FAILURE = "backend failure"
_INVALID_ALIAS = "invalid alias"
_FIELDS = {"name", "content", "origin", "source", "revision"}
_MISS_FIELDS = {"version", "state", "source", "name", "revision"}
_TEMPLATE_FIELDS = {"version", "state", "template"}
_RESOLVED_FIELDS = {"version", "state", "source", "name", "template"}
_RESOLVED_MISS_FIELDS = {"version", "state", "source", "name"}
_RESOLVER_REVISION = "@resolved"


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


def _without_untrusted_exception[T](operation: Callable[[], T]) -> T | object:
    try:
        return operation()
    except Exception:  # Cache backends and serialized payloads are untrusted.
        return _FAILURE


def _resolve_cache_alias(alias: object) -> tuple[BaseCache | None, str | None]:
    if not isinstance(alias, str) or not alias or alias not in settings.CACHES:
        return None, _INVALID_ALIAS
    backend = _without_untrusted_exception(lambda: caches[alias])
    if backend is _FAILURE:
        return None, _BACKEND_FAILURE
    if not isinstance(backend, BaseCache):
        return None, _INVALID_ALIAS
    return backend, None


def template_cache_key(namespace: str, source: str, name: str, revision: str) -> str:
    """Return a backend-safe key for one raw template revision."""
    components = json.dumps(
        [namespace, source, name, revision],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode(errors="surrogatepass")
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
        backend, alias_error = _resolve_cache_alias(alias)
        if alias_error is not None:
            source = f"cache:{alias}" if alias_error == _BACKEND_FAILURE else "cache"
            raise SourceUnavailable(source, alias_error)
        self.namespace = namespace
        self.alias = alias
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self.backend = backend

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
        payload = _without_untrusted_exception(
            lambda: self.backend.get(self.key(source, name, revision), _ABSENT)
        )
        if payload is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if payload is _ABSENT:
            return None
        return self._decode(payload, source, name, revision)

    def set(self, template: ResolvedTemplate) -> None:
        payload = json.dumps(
            {"version": 1, "state": "template", "template": asdict(template)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = _without_untrusted_exception(
            lambda: self.backend.set(
                self.key(template.source, template.name, template.revision),
                payload,
                timeout=self.ttl,
            )
        )
        if result is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def set_miss(self, source: str, name: str, revision: str) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "state": "miss",
                "source": source,
                "name": name,
                "revision": revision,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = _without_untrusted_exception(
            lambda: self.backend.set(
                self.key(source, name, revision), payload, timeout=self.negative_ttl
            )
        )
        if result is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def get_resolved(self, source: str, name: str) -> CacheEntry | None:
        """Return the latest raw result cached for one configured source."""
        payload = _without_untrusted_exception(
            lambda: self.backend.get(
                self.key(source, name, _RESOLVER_REVISION), _ABSENT
            )
        )
        if payload is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if payload is _ABSENT:
            return None
        return self._decode_resolved(payload, source, name)

    def set_resolved(self, source: str, name: str, template: ResolvedTemplate) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "state": "resolved",
                "source": source,
                "name": name,
                "template": asdict(template),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._set_resolved(source, name, payload, self.ttl)

    def set_resolved_miss(self, source: str, name: str) -> None:
        payload = json.dumps(
            {"version": 1, "state": "source-miss", "source": source, "name": name},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._set_resolved(source, name, payload, self.negative_ttl)

    def _set_resolved(self, source: str, name: str, payload: str, timeout: int) -> None:
        result = _without_untrusted_exception(
            lambda: self.backend.set(
                self.key(source, name, _RESOLVER_REVISION), payload, timeout=timeout
            )
        )
        if result is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _decode_resolved(self, payload: object, source: str, name: str) -> CacheEntry:
        def decode() -> CacheEntry:
            if not isinstance(payload, str):
                raise TypeError
            value = json.loads(payload, object_pairs_hook=_unique_object)
            if type(value.get("version")) is not int or value["version"] != 1:
                raise ValueError
            if value.get("state") == "source-miss":
                if (
                    set(value) != _RESOLVED_MISS_FIELDS
                    or not all(
                        isinstance(value[field], str) for field in ("source", "name")
                    )
                    or (value["source"], value["name"]) != (source, name)
                ):
                    raise ValueError
                return CACHE_MISS
            if value.get("state") != "resolved" or set(value) != _RESOLVED_FIELDS:
                raise ValueError
            template = value["template"]
            if (
                not isinstance(template, dict)
                or set(template) != _FIELDS
                or not all(isinstance(item, str) for item in template.values())
                or template["name"] != name
                or (value["source"], value["name"]) != (source, name)
            ):
                raise ValueError
            return CacheEntry(ResolvedTemplate(**template))

        entry = _without_untrusted_exception(decode)
        if entry is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
        return entry

    def _decode(
        self, payload: object, source: str, name: str, revision: str
    ) -> CacheEntry:
        def decode() -> CacheEntry:
            if not isinstance(payload, str):
                raise TypeError
            value = json.loads(payload, object_pairs_hook=_unique_object)
            if type(value.get("version")) is not int or value["version"] != 1:
                raise ValueError
            if value.get("state") == "miss":
                if (
                    set(value) != _MISS_FIELDS
                    or not all(
                        isinstance(value[field], str)
                        for field in ("source", "name", "revision")
                    )
                    or (value["source"], value["name"], value["revision"])
                    != (source, name, revision)
                ):
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

        entry = _without_untrusted_exception(decode)
        if entry is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
        return entry
