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
_GENERATION_DOMAIN = "dj-hyperview:generation:v2"
_CLAIM_DOMAIN = "dj-hyperview:generation-claim:v1"
_GENERATION_KINDS = {"r": "root", "s": "successor"}


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A present cache entry containing a template or an explicit miss."""

    template: ResolvedTemplate | None

    @property
    def is_miss(self) -> bool:
        """Report whether this entry represents a cached source miss.

        Returns:
            Whether the entry is an explicit source miss.
        """
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
    """Return a backend-safe key for one raw template revision.

    Args:
        namespace: Cache namespace.
        source: Source identity.
        name: Canonical template name.
        revision: Source revision.

    Returns:
        A deterministic backend-safe cache key.
    """
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
        """Initialize a raw-template cache boundary.

        Args:
            namespace: Namespace isolating this consumer's entries.
            alias: Configured Django cache alias.
            ttl: Lifetime for cached template content.
            negative_ttl: Lifetime for cached source misses.

        Raises:
            ValueError: If the namespace or a timeout is invalid.
            SourceUnavailable: If the configured backend cannot be initialized.
        """
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
        """Create a template cache from current package settings.

        Args:
            namespace: Namespace isolating this package consumer's cache entries.

        Returns:
            A cache configured from current package settings.
        """
        config = get_settings().cache
        return cls(
            namespace,
            alias=config.alias,
            ttl=config.ttl,
            negative_ttl=config.negative_ttl,
        )

    def key(self, source: str, name: str, revision: str) -> str:
        """Return the backend-safe key for one cached template identity.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            revision: Source or generation revision.

        Returns:
            A deterministic backend-safe cache key.
        """
        return template_cache_key(self.namespace, source, name, revision)

    def _store(self, key: str, value: object, timeout: int | None) -> None:
        result = _without_untrusted_exception(
            lambda: self.backend.set(key, value, timeout=timeout)
        )
        if result is _FAILURE or result is False:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _delete(self, key: str) -> None:
        result = _without_untrusted_exception(lambda: self.backend.delete(key))
        if result is not True:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _generation_key(self, name: str) -> str:
        return self.key("@generation", name, "@token")

    def _claim_key(self, name: str, generation: str) -> str:
        return self.key("@generation-claim", name, generation)

    def _claim_value(self, name: str, generation: str, kind: str) -> str:
        identity = json.dumps(
            [_CLAIM_DOMAIN, self.namespace, name, generation, kind],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(errors="surrogatepass")
        return json.dumps(
            {
                "version": 1,
                "kind": kind,
                "identity": hashlib.sha256(identity).hexdigest(),
            },
            separators=(",", ":"),
        )

    def _read_claim(self, name: str, generation: str) -> str:
        kind = self._generation_kind(generation)
        value = _without_untrusted_exception(
            lambda: self.backend.get(self._claim_key(name, generation), _ABSENT)
        )
        if value is _FAILURE or value is _ABSENT:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if value == self._claim_value(name, generation, kind):
            return kind
        raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")

    def _candidate(self, name: str, current: str | None) -> str:
        entropy = _without_untrusted_exception(lambda: secrets.token_hex(16))
        if type(entropy) is not str:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        material = json.dumps(
            [_GENERATION_DOMAIN, self.namespace, name, current, entropy],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(errors="surrogatepass")
        prefix = "r" if current is None else "s"
        return prefix + hashlib.sha256(material).hexdigest()[:32]

    def _claim_generation(self, name: str, current: str | None) -> str:
        kind = "root" if current is None else "successor"
        for _ in range(_GENERATION_ATTEMPTS):
            candidate = self._candidate(name, current)
            if self._generation_kind(candidate) != kind:
                raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
            claimed = _without_untrusted_exception(
                lambda candidate=candidate: self.backend.add(
                    self._claim_key(name, candidate),
                    self._claim_value(name, candidate, kind),
                    timeout=None,
                )
            )
            if claimed is True:
                return candidate
            if claimed is False:
                self._read_claim(name, candidate)
                continue
            break
        raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def _read_generation(self, name: str) -> str:
        token = _without_untrusted_exception(
            lambda: self.backend.get(self._generation_key(name), _ABSENT)
        )
        if token is _FAILURE or token is _ABSENT:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if not self._valid_generation(token):
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
        return token

    @staticmethod
    def _valid_generation(token: object) -> bool:
        return (
            type(token) is str
            and len(token) == 33
            and token[0] in _GENERATION_KINDS
            and all(character in "0123456789abcdef" for character in token[1:])
        )

    def _generation_kind(self, token: object) -> str:
        if not self._valid_generation(token):
            raise SourceUnavailable(f"cache:{self.alias}", "invalid payload")
        return _GENERATION_KINDS[token[0]]

    def generation(self, name: str) -> str:
        """Return the shared generation token for a canonical template name.

        Args:
            name: Canonical template name.

        Returns:
            The current shared generation token.

        Raises:
            SourceUnavailable: If the backend or generation payload is invalid.
        """
        key = self._generation_key(name)
        token = _without_untrusted_exception(lambda: self.backend.get(key, _ABSENT))
        if token is _ABSENT:
            candidate = self._claim_generation(name, None)
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
        self._read_claim(name, token)
        return token

    def invalidate(self, name: str) -> None:
        """Rotate a template generation without deleting backend-specific keys.

        Args:
            name: Canonical template name.

        Raises:
            SourceUnavailable: If generation rotation cannot be confirmed.
        """
        current = self.generation(name)
        candidate = self._claim_generation(name, current)
        self._store(self._generation_key(name), candidate, None)
        confirmed = self._read_generation(name)
        if confirmed != candidate:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")

    def get(self, source: str, name: str, revision: str) -> CacheEntry | None:
        """Read one exact raw-template cache entry.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            revision: Exact source revision.

        Returns:
            The cached entry when present, otherwise absence.

        Raises:
            SourceUnavailable: If the backend or cached payload is invalid.
        """
        payload = _without_untrusted_exception(
            lambda: self.backend.get(self.key(source, name, revision), _ABSENT)
        )
        if payload is _FAILURE:
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure")
        if payload is _ABSENT:
            return None
        return self._decode(payload, source, name, revision)

    def set(self, template: ResolvedTemplate) -> None:
        """Store one resolved raw template.

        Args:
            template: Resolved raw template to cache.
        """
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
        """Store an explicit miss for one raw-template identity.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            revision: Exact source revision.
        """
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
        """Return the latest raw result cached for one configured source.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            generation: Optional shared generation token.

        Returns:
            The latest cached source result when present, otherwise absence.

        Raises:
            SourceUnavailable: If the backend or cached payload is invalid.
        """
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
        """Store the latest resolved raw result for a configured source.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            template: Resolved raw template to cache.
            generation: Optional shared generation token.
        """
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
        """Store a latest-result miss for a configured source.

        Args:
            source: Stable source identity.
            name: Canonical template name.
            generation: Optional shared generation token.
        """
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
        key = self.key(source, name, self._resolved_revision(generation))
        self._store(key, payload, timeout)
        if generation is not None:
            self._confirm_publication(name, generation, key)

    def _confirm_publication(self, name: str, generation: str, key: str) -> None:
        try:
            current = self._read_generation(name)
        except SourceUnavailable:
            self._preserve_claim_and_delete(name, generation, key)
            raise SourceUnavailable(f"cache:{self.alias}", "backend failure") from None
        if current != generation:
            clean = self._discard_replaced_publication(key)
            reason = "generation changed" if clean else "backend failure"
            raise SourceUnavailable(f"cache:{self.alias}", reason)

    def _preserve_claim_and_delete(self, name: str, generation: str, key: str) -> bool:
        clean = True
        try:
            self._read_claim(name, generation)
        except SourceUnavailable:
            clean = False
        try:
            self._delete(key)
        except SourceUnavailable:
            clean = False
        return clean

    def _discard_replaced_publication(self, key: str) -> bool:
        try:
            self._delete(key)
        except SourceUnavailable:
            return False
        return True

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
    """Invalidate future cached lookups for canonical template names.

    Args:
        *names: Canonicalizable template names.
    """
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
