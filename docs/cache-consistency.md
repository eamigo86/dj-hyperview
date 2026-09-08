# Cache consistency

Enable raw-template caching only when repeated source reads justify another
stateful dependency. With no `CACHE` section, resolution never initializes a
Django cache backend.

The backend must provide atomic `add()` and coherent reads for generation
barriers. Django's `FileBasedCache` and its subclasses are rejected with
`dj_hyperview.E018`: their separate existence check and write can resurrect an
invalidated template. Direct `TemplateCache` use raises `SourceUnavailable` with
reason `unsupported backend`. `FAILURE_MODE="bypass"` does not override this
configuration error.

Do not point `CACHE.ALIAS` at Django's `DummyCache`. That backend intentionally
stores nothing and cannot satisfy generation or invalidation guarantees. Omit
the `CACHE` section when caching should be disabled.

## Opt in

Configure a normal Django cache alias and bind Hyperview to it:

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hyperview-docs",
    },
}

HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    "CACHE": {
        "ALIAS": "default",
        "NAMESPACE": "my-service-v1",
        "TTL": 300,
        "NEGATIVE_TTL": 15,
        "FAILURE_MODE": "bypass",
    },
}
```

The cache stores raw content and source misses, including valid empty content.
It never stores compiled templates. Namespace, source identity, canonical name,
revision, and generation keep entries isolated.

## Select a Redis database

`CACHE.ALIAS` can point to a compatible stateful cache configured in Django.
Redis host, credentials, logical database, and transport belong in `CACHES`;
dj-hyperview only resolves the alias. `LocMemCache` provides atomic operations
inside one process, but a multi-process deployment needs a shared backend such
as Redis so every worker observes invalidations.

For example, dedicate Redis logical database 3 to Hyperview templates:

```python
CACHES = {
    "hyperview": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/3",
    },
}

HYPERVIEW = {
    "CACHE": {
        "ALIAS": "hyperview",
        "NAMESPACE": "my-service-v1",
        "TTL": 300,
        "NEGATIVE_TTL": 15,
        "FAILURE_MODE": "bypass",
    },
}
```

Change the final URL segment to select another logical database supported by
the configured Redis deployment. A separate alias may also point to another
Redis server or cluster. Backend-specific restrictions still apply; the
package does not bypass limitations imposed by Django's backend or Redis.

When upgrading from `FileBasedCache`, either remove the Hyperview `CACHE`
section or configure a compatible backend before deployment. Use a fresh
Hyperview namespace rather than copying old cache entries; do not clear a
shared Django cache to migrate this package's entries.

`bypass` returns authoritative source data when ordinary cache reads or writes
fail. `raise` reports `SourceUnavailable` instead. Neither mode hides a real
source failure.

An explicitly bound `DatabaseSource` never publishes a source read while its
database connection is inside a transaction. Existing cache hits remain
available, but an older transaction snapshot cannot pin stale content under a
newer generation. Applications using `ATOMIC_REQUESTS` therefore fall back to
database reads for cache misses without sharing those snapshot-local results.

## Invalidate after publication

Database services and model signals schedule invalidation after commit. For a
custom publisher, rotate the generation only after its write commits:

```python
from django.db import transaction

from dj_hyperview import invalidate_templates

transaction.on_commit(
    lambda: invalidate_templates("screens/home.xml"),
    using="default",
)
```

Invalidation is a shared fail-closed barrier even when reads use `bypass`. A
multi-name call validates all names first, but rotations are not atomic; retry
the whole requested set after a reported failure.

Unknown template misses create no cache metadata. The first successful lookup
through a cacheable source creates one non-expiring generation key; later
invalidations rotate that same key with a new random token. Raw content and
negative entries remain bounded by their configured TTLs. If the generation key
is externally evicted, the next successful source lookup establishes a fresh
generation and old raw entries remain unreachable.

## Invalidate after a filesystem deploy

A filesystem deploy does not invalidate already cached source content. In-place
file changes and atomic root symlink swaps remain stale until their TTL expires.
After every filesystem deploy, call `invalidate_templates` for the affected
canonical names or rotate `CACHE.NAMESPACE` for the whole release. Namespace
rotation is simpler for immutable deployments; targeted invalidation avoids a
cold cache when only a few screens changed.
