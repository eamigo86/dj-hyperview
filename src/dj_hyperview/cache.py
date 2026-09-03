"""Typed raw-template caching through Django's cache framework."""

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import BaseCache

from .conf import get_settings
from .exceptions import SourceUnavailable
from .sources import ResolvedTemplate, canonicalize_template_name

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
_GENERATION_ATTEMPTS = 8
_CLAIM_VALUE = "claimed"


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

    def _store(self, key: str, value: object, timeout: int | None) -> None:
        result = _without_untrusted_exception(
            lambda: self.backend.set(key, value, timeout=timeout)
        )
        if result is _FAILURE or result is False:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _claim_key(self, name: str, generation: str) -> str:
        return self.key("@generation-claim", name, generation)

    def _retain_claim(self, name: str, generation: str) -> None:
        self._store(
            self._claim_key(name, generation),
            _CLAIM_VALUE,
            max(self.ttl, self.negative_ttl),
        )

    def _ensure_claim(self, name: str, generation: str) -> None:
        result = _without_untrusted_exception(
            lambda: self.backend.add(
                self._claim_key(name, generation),
                _CLAIM_VALUE,
                timeout=max(self.ttl, self.negative_ttl),
            )
        )
        if type(result) is not bool:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _claim_generation(self, name: str) -> str:
        for _ in range(_GENERATION_ATTEMPTS):
            candidate = secrets.token_hex(16)
            if not self._valid_generation(candidate):
                break
            claimed = _without_untrusted_exception(
                lambda candidate=candidate: self.backend.add(
                    self._claim_key(name, candidate),
                    _CLAIM_VALUE,
                    timeout=max(self.ttl, self.negative_ttl),
                )
            )
            if claimed is True:
                return candidate
            if claimed is not False:
                break
        raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    @staticmethod
    def _valid_generation(token: object) -> bool:
        return (
            type(token) is str
            and len(token) == 32
            and all(character in "0123456789abcdef" for character in token)
        )

    def generation(self, name: str) -> str:
        """Return the shared generation token for a canonical template name."""
        key = self.key("@generation", name, "@token")
        token = _without_untrusted_exception(lambda: self.backend.get(key, _ABSENT))
        if token is _ABSENT:
            candidate = self._claim_generation(name)
            added = _without_untrusted_exception(
                lambda: self.backend.add(key, candidate, timeout=None)
            )
            token = (
                candidate
                if added is True
                else _without_untrusted_exception(
                    lambda: self.backend.get(key, _ABSENT)
                )
                if added is False
                else _FAILURE
            )
        if token is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if not self._valid_generation(token):
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
        self._ensure_claim(name, token)
        return token

    def invalidate(self, name: str) -> None:
        """Rotate a template generation without deleting backend-specific keys."""
        self.generation(name)
        candidate = self._claim_generation(name)
        self._store(self.key("@generation", name, "@token"), candidate, None)

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
        self._store(
            self.key(template.source, template.name, template.revision),
            payload,
            self.ttl,
        )

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
        self._store(self.key(source, name, revision), payload, self.negative_ttl)

    def get_resolved(
        self, source: str, name: str, generation: str | None = None
    ) -> CacheEntry | None:
        """Return the latest raw result cached for one configured source."""
        revision = self._resolved_revision(generation)
        payload = _without_untrusted_exception(
            lambda: self.backend.get(self.key(source, name, revision), _ABSENT)
        )
        if payload is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if payload is _ABSENT:
            return None
        return self._decode_resolved(payload, source, name)

    def set_resolved(
        self,
        source: str,
        name: str,
        template: ResolvedTemplate,
        generation: str | None = None,
    ) -> None:
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
        self._set_resolved(source, name, payload, self.ttl, generation)

    def set_resolved_miss(
        self, source: str, name: str, generation: str | None = None
    ) -> None:
        payload = json.dumps(
            {"version": 1, "state": "source-miss", "source": source, "name": name},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._set_resolved(source, name, payload, self.negative_ttl, generation)

    @staticmethod
    def _resolved_revision(generation: str | None) -> str:
        return _RESOLVER_REVISION if generation is None else f"@resolved:{generation}"

    def _set_resolved(
        self,
        source: str,
        name: str,
        payload: str,
        timeout: int,
        generation: str | None,
    ) -> None:
        if generation is not None:
            self._retain_claim(name, generation)
        self._store(
            self.key(source, name, self._resolved_revision(generation)),
            payload,
            timeout,
        )

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


def invalidate_templates(*names: str) -> None:
    """Invalidate future cached lookups for canonical template names."""
    canonical = tuple(dict.fromkeys(canonicalize_template_name(name) for name in names))
    if not canonical:
        return
    raw = getattr(settings, "HYPERVIEW", {})
    if isinstance(raw, Mapping) and not raw.get("CACHE"):
        return
    config = get_settings().cache
    cache = TemplateCache.from_settings(config.namespace)
    for name in canonical:
        cache.invalidate(name)
