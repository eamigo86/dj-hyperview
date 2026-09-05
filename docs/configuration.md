# Configuration

Point dj-hyperview at templates owned by your Django project:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "SOURCES": [
        {"BACKEND": "dj_hyperview.sources.FileSystemSource"},
    ],
}
```

Resolve a canonical relative POSIX name through the public API:

```python
from dj_hyperview import TemplateResolver

template = TemplateResolver.from_settings().resolve("screens/home.xml")
```

Sources are queried in order and the first hit wins. Absolute paths, traversal,
backslashes, empty path segments, control characters, and surrogates are
rejected. Omitting `CACHE` keeps caching disabled and avoids cache backend
initialization.

`DummyCache` is not a valid Hyperview cache backend because it cannot preserve
generation state. To disable caching, omit `CACHE` instead of selecting a dummy
cache alias.

## Source backend options

`OPTIONS` is an optional mapping. The resolver passes its entries to the
configured backend constructor as keyword arguments. The bundled backends
support the following options:

| Backend | Option | Default | Meaning |
| --- | --- | --- | --- |
| `FileSystemSource` | `template_dirs` | `HYPERVIEW["TEMPLATE_DIRS"]` | Ordered template roots for this source instance, supplied as an iterable of strings or `Path` objects. Earlier roots win. |
| `DatabaseSource` | `using` | `None` | A configured Django database alias. A fixed alias permits source caching; omitting it delegates to Django database routing and disables source caching because the selected database can vary. |

`template_dirs` must be a list, tuple, or another iterable of roots. Do not pass
a single string or Path: scalar paths are rejected instead of being interpreted
as collections.

For example, one filesystem source can override the global roots:

```python
HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "SOURCES": [
        {
            "BACKEND": "dj_hyperview.sources.FileSystemSource",
            "OPTIONS": {
                "template_dirs": [BASE_DIR / "tenant-hyperview"],
            },
        },
    ],
}
```

A custom backend defines its own supported `OPTIONS` through its constructor.
There is no package-wide list for third-party options; unknown keyword
arguments fail during source initialization. See [Filesystem](filesystem.md)
and [Database and admin](database-admin.md) for backend-specific behavior.

## Template engine behavior

Named templates use dj-hyperview's dedicated resolver-backed template engine
by default. That engine copies template-language options, such as context
processors and `string_if_invalid`, from the first configured DjangoTemplates backend.
It deliberately replaces that backend's loaders so template lookup
still follows `HYPERVIEW["SOURCES"]` and never falls through to unrelated
Django template directories.

The response path does not switch engines when `SOURCES` is empty. An empty
source list therefore produces a normal missing-template result. To opt out and
select a consumer Django engine explicitly, pass its alias, for example
`using="django"`, to `HyperviewTemplateResponse`.

Continue with [Filesystem](filesystem.md), [Database and admin](database-admin.md),
[Cache consistency](cache-consistency.md), [Security](security.md),
[Testing](testing.md), [Release and rollback](release-rollback.md), or the
[public Python API](api-reference.md). Return to the
[documentation home](index.md).
