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

## Choose which sources are cached

Caching is an explicit opt-in per source. The resolver caches a source only when
its `cacheable` attribute is exactly `True`. A source without the attribute, or
with any other value, is read on every lookup and creates no cache entries.

| Source | Cached |
| --- | --- |
| `FileSystemSource` | Yes: it reports `cacheable = True`. |
| `DatabaseSource` with an explicit `using` alias | Yes. |
| `DatabaseSource` without `using` | No: Django routing can select a different database per request. |
| A subclass of either that overrides `resolve()` | Only when it declares `cacheable = True` itself. |
| A custom source | Only when it declares `cacheable = True`. |

A custom source opts in with a class attribute, an instance attribute, or a
property that returns `True`:

```python
from dj_hyperview import ResolvedTemplate


class ReleaseBucketSource:
    # The content depends only on the template name and the constructor options.
    cacheable = True

    def __init__(self, *, bucket: str) -> None:
        self.bucket = bucket

    def resolve(self, name: str) -> ResolvedTemplate | None: ...
```

Declare `cacheable = True` only when the content returned for a name depends
solely on that name and the constructor options. A cache entry is keyed by the
namespace, the source's position, class and constructor options, the canonical
name, the revision, and the generation. Nothing about the request, user, tenant,
language, or any other ambient state is part of the key. A source that reads such
state, for example a `ContextVar` that selects the current tenant, thread-local
data, or the active translation, must not opt in: the first caller's content
would be served to every other caller until the entry expires or is invalidated.

Subclasses of your own sources inherit the attribute, so a subclass whose
`resolve()` reads more than its parent does must set `cacheable = False`. The
built-in sources are stricter. `FileSystemSource` and `DatabaseSource` report
`cacheable` as `True` only while `resolve()` is their own implementation, so a
subclass that overrides `resolve()`, for example to select a tenant's directory, is
not cached until it declares `cacheable = True` itself. Do that only when the rule
above still holds. Both built-in sources accept the class attribute and the
instance attribute (`self.cacheable = False` in `__init__`).

Earlier betas cached every custom source that lacked a private marker. Custom
sources are no longer cached until they declare `cacheable = True`. Add the
declaration only after checking the rule above; otherwise the only effect of the
change is that the source is read on every lookup.

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

A cache that bypass silently skips would never be noticed, so each swallowed
failure logs one `WARNING` on the `dj_hyperview.cache` logger:
`template_cache_bypassed operation=<operation> reason=<reason>`. The operation is
`initialize`, `generation`, `read` or `write`, and the reason is one of the fixed
descriptions `SourceUnavailable` carries, such as `backend failure`, `invalid
payload`, `invalid alias` or `unsupported backend`. The message never contains a
template name, a cache key, a source identity or the backend's own error. A cache
that works logs nothing. A source that declares `cacheable = True` but has
constructor options the cache cannot identify, such as an arbitrary object, is
never cached; building the resolver logs one `INFO` record,
`template_source_uncacheable source_index=<n> reason=unsupported_options`.

A source must return `None` or a `ResolvedTemplate` named exactly as requested,
with `str` fields. Anything else raises `SourceUnavailable` with the reason
`invalid resolved template` in both failure modes, because the cache could not read
the entry back.

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
