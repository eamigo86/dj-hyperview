# Cache consistency

Enable raw-template caching only when repeated source reads justify another
stateful dependency. With no `CACHE` section, resolution never initializes a
Django cache backend.

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

`bypass` returns authoritative source data when ordinary cache reads or writes
fail. `raise` reports `SourceUnavailable` instead. Neither mode hides a real
source failure.

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

Each claimed generation leaves a shared non-expiring tombstone. This prevents
token reuse while old raw entries survive, at the cost of one small marker per
candidate until the namespace or backend is retired. External eviction of both
generation metadata and tombstones is outside the generic-cache guarantee.
