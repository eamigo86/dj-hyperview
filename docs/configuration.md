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

## Settings reference

All keys are optional except the fields inside each configured source. Cache
defaults apply only when `CACHE` is a non-empty mapping; omitting it or using an
empty mapping disables Hyperview caching.

| Setting | Type | Default | Meaning |
| --- | --- | --- | --- |
| `HYPERVIEW` | Mapping | `{}` | Package configuration in Django settings. An absent or empty mapping configures no sources and no cache. |
| `TEMPLATE_DIRS` | List or tuple of strings or `Path` objects | `()` | Ordered project-owned roots inherited by `FileSystemSource` instances that do not override `template_dirs`. Missing roots emit an operational warning and are skipped at runtime. |
| `SOURCES` | Sequence of mappings | `()` | Ordered source definitions. The first source that resolves a canonical template name wins. An empty sequence resolves no templates. |
| `SOURCES[].BACKEND` | Dotted import path | Required | Importable source class for one entry. Built-in paths are shown below. |
| `SOURCES[].OPTIONS` | Mapping | `{}` | Keyword arguments passed to that source class constructor. Supported keys are backend-specific. |
| `CACHE` | Mapping | Omitted (disabled) | Enables raw-source and source-miss caching when non-empty. It does not cache compiled templates. |
| `CACHE.ALIAS` | String | `"default"` | Configured stateful Django cache alias. `DummyCache` is unsupported. |
| `CACHE.NAMESPACE` | Non-empty string | `"dj-hyperview"` | Prefix domain that isolates all Hyperview cache keys. Change it to abandon previously published cache state. |
| `CACHE.TTL` | Integer greater than or equal to 1 | 300 seconds | Lifetime of successful raw-template and resolved-source entries. Generation keys do not expire. |
| `CACHE.NEGATIVE_TTL` | Integer greater than or equal to 0 | 15 seconds | Lifetime of explicit source misses. Zero disables effective negative-entry retention. |
| `CACHE.FAILURE_MODE` | `bypass` or `raise` | `"bypass"` | `bypass` falls back to authoritative sources after ordinary cache failures; `raise` reports `SourceUnavailable`. Invalidation remains fail-closed in both modes. |
| `VALIDATION` | Mapping | `{}` | Source-safety and final rendered-document validation policy. |
| `VALIDATION.MODE` | `publish`, `render`, or `publish_and_render` | `"publish_and_render"` | `publish` performs mandatory raw-source safety checks and skips final XML/schema validation. `render` and `publish_and_render` also validate the final rendered document; the latter is the explicit combined default policy. |
| `VALIDATION.SCHEMA` | `None`, filesystem path, callable, or dotted callable path | `None` | Optional final-document validator. A path must identify a local single-file XSD 1.0 schema. A callable receives the rendered string and rejects it by returning `False` or raising. |
| `VALIDATION.MAX_BYTES` | Positive integer | 1,000,000 bytes | Maximum UTF-8 size accepted for raw source and the final rendered document. |
| `VALIDATION.MAX_DEPTH` | Positive integer | 64 levels | Maximum final XML element depth, with an absolute maximum of 256 imposed by the parser safety ceiling. |
| `VALIDATION.MAX_NODES` | Positive integer | 20,000 nodes | Maximum number of elements in the final parsed XML document. |
| `ADMIN` | Mapping | `{}` | Optional Django Admin enhancements. |
| `ADMIN.EDITOR` | Boolean | `False` | Replace the database template textarea with the optional HXML-aware Ace editor. Requires the `editor` extra and `django_ace` in `INSTALLED_APPS`. |
| `EXTRA_SCHEMAS` | List or tuple of strings or `Path` objects | `()` | Local XSD roots merged into the bundled Hyperview 0.110.0 registry and completion catalog. URLs are rejected. |

Raw-source size, encoding, and forbidden-declaration checks cannot be disabled
by `VALIDATION.MODE`. Final parsing, depth, node, and schema checks run only for
`render` and `publish_and_render`.

## Hyperview XSD 1.1 validation

Install the optional schema profile and select the bundled validator:

```bash
uv add "dj-hyperview[schema]"
```

```python
HYPERVIEW = {
    "EXTRA_SCHEMAS": [BASE_DIR / "schema" / "hypertodo.xsd"],
    "VALIDATION": {
        "SCHEMA": "dj_hyperview.validate_hyperview_schema",
    },
}
```

The validator uses the versioned Hyperview 0.110.0 XSD registry included in the
wheel. Project schemas can declare namespaced custom elements and import the
Hyperview namespace. Includes, imports, and redefines may reference only local
files below the configured schema's own directory. Remote references and paths
that escape that directory are rejected.

Schema registries and completion catalogs are loaded lazily and cached by
resolved path, file size, and nanosecond modification time. Changes to
`HYPERVIEW` clear those caches. An editor process notices a saved schema file on
its next request because the file fingerprint changes.

Follow [Add custom HXML elements](custom-schemas.md) for the complete namespace,
registration, validation, and autocomplete workflow. Its
[custom component schema example](examples/hypertodo.xsd) defines
`app:swipe-row` and `app:swipe-action`.

## Admin editor

The enhanced editor is deliberately separate from database storage:

```bash
uv add "dj-hyperview[editor]"
```

```python
INSTALLED_APPS = [
    "django_ace",
    "dj_hyperview",
    "dj_hyperview.contrib.database",
]

HYPERVIEW = {
    "ADMIN": {"EDITOR": True},
}
```

Enabling the setting without the extra, or without `django_ace` in
`INSTALLED_APPS`, produces an actionable Django system-check error. When the
setting is false, the standard Django textarea remains unchanged.

## Source backend options

`OPTIONS` is an optional mapping. The resolver passes its entries to the
configured backend constructor as keyword arguments. The bundled backends
support the following options:

| Backend | Option | Default | Meaning |
| --- | --- | --- | --- |
| `FileSystemSource` | `template_dirs` | `HYPERVIEW["TEMPLATE_DIRS"]` | Ordered template roots for this source instance, supplied as a list or tuple of strings or `Path` objects. Earlier roots win. |
| `DatabaseSource` | `using` | `None` | A configured Django database alias. A fixed alias permits source caching; omitting it delegates to Django database routing and disables source caching because the selected database can vary. |

`template_dirs` must be a list or tuple of roots. Do not pass a single string,
Path, generator, set, or other iterable: only reusable ordered collections are
accepted. A root that is temporarily unavailable emits a system-check warning;
resolution continues through later roots.

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
by default. That engine copies only `context_processors`, `string_if_invalid`,
`builtins`, and `libraries` from the first configured DjangoTemplates backend
or subclass. It deliberately isolates security-sensitive options such as
`autoescape` and replaces the backend's loaders, so template lookup still
follows `HYPERVIEW["SOURCES"]` and never falls through to unrelated Django
template directories.

The response path does not switch engines when `SOURCES` is empty. An empty
source list therefore produces a normal missing-template result. To opt out and
select a consumer Django engine explicitly, pass its alias, for example
`using="django"`, to `HyperviewTemplateResponse`. The `dj_hyperview.W005`
system check calls out an absent configuration or an explicitly empty source
list. Temporarily unavailable filesystem roots emit `dj_hyperview.W006` and do
not prevent later roots from resolving templates.

Continue with [Filesystem](filesystem.md), [Database and admin](database-admin.md),
[Cache consistency](cache-consistency.md), [Security](security.md),
[Testing](testing.md), [Release and rollback](release-rollback.md), or the
[public Python API](api-reference.md). Return to the
[documentation home](index.md).
