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

`FileBasedCache` and its subclasses are rejected with `dj_hyperview.E018`, even
when `CACHE.FAILURE_MODE` is `bypass`: their non-atomic `add()` cannot protect
template generations. Select a backend with atomic add and coherent reads, or
omit `CACHE`. This is a configuration error, not a recoverable backend outage.

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
| `CACHE.ALIAS` | String | `"default"` | Django cache alias with atomic add and coherent reads. `DummyCache` and `FileBasedCache` are unsupported. |
| `CACHE.NAMESPACE` | Non-empty string | `"dj-hyperview"` | Prefix domain that isolates all Hyperview cache keys. Change it to abandon previously published cache state. |
| `CACHE.TTL` | Integer greater than or equal to 1 | 300 seconds | Lifetime of successful raw-template and resolved-source entries. Generation keys do not expire. |
| `CACHE.NEGATIVE_TTL` | Integer greater than or equal to 0 | 15 seconds | Lifetime of explicit source misses. Zero disables effective negative-entry retention. |
| `CACHE.FAILURE_MODE` | `bypass` or `raise` | `"bypass"` | `bypass` falls back to authoritative sources after ordinary cache failures; `raise` reports `SourceUnavailable`. Invalidation remains fail-closed in both modes. |
| `VALIDATION` | Mapping | `{}` | Resource limits for mandatory source safety and automatic rendered validation. |
| `VALIDATION.MAX_BYTES` | Positive integer | 1,000,000 bytes | Maximum UTF-8 size accepted for raw source and the final rendered document. |
| `VALIDATION.MAX_DEPTH` | Positive integer | 64 levels | Maximum final XML element depth, with an absolute maximum of 256 imposed by the parser safety ceiling. |
| `VALIDATION.MAX_NODES` | Positive integer | 20,000 nodes | Maximum number of elements in the final parsed XML document. |
| `ADMIN` | Mapping | `{}` | Optional Django Admin enhancements. |
| `ADMIN.EDITOR` | Boolean | `False` | Replace the database template textarea with the optional HXML-aware Ace editor with one conservative Format and Validate action. Requires the `editor` extra and `django_ace` in `INSTALLED_APPS`. |
| `ADMIN.PERMISSION` | Callable or dotted callable path | Superuser-only callback | Authoritative mutation policy for adding, changing, and deleting stored templates. The callable receives the current `HttpRequest` and must return the literal boolean `True`; exceptions and non-boolean results deny access. |
| `EXTRA_SCHEMAS` | List or tuple of strings or `Path` objects | `()` | Local XSD roots merged into the bundled Hyperview 0.110.0 registry and completion catalog. URLs are rejected. |
| `SCHEMA_EXTENSIONS` | Mapping | `{}` | Typed app-owned behavior and element-attribute declarations; standard declarations cannot be replaced. |

## Automatic Hyperview XSD 1.1 validation

The package always validates final rendered documents and fragments against one
corrected Hyperview 0.110.0 registry. Raw source remains context-free: safety and
Django compilation do not invent variables or render a scenario. Configuring
`VALIDATION` changes only `MAX_BYTES`, `MAX_DEPTH`, and `MAX_NODES`; it cannot
skip or replace standard validation.

`SCHEMA_PROFILE`, `VALIDATION.MODE`, and `VALIDATION.SCHEMA` are removed settings.
Their presence produces a configuration error, including their former default
values. Delete them rather than replacing them with a new selector. Register
only application extensions when needed:

```python
HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "EXTRA_SCHEMAS": [BASE_DIR / "schema" / "hypertodo.xsd"],
}
```

Project schemas can declare namespaced custom elements and import the Hyperview
namespace. Includes, imports, and redefines may reference only plain local files
below their root schema's directory. Remote references, traversal, percent-encoded
locations, backslash/URI ambiguity, DTD/entities, and arbitrary `xs:override` are
rejected. Each extra schema may reference at most 256 distinct files, including
its root; cycles are visited once and symlink escapes remain forbidden.

Validation, static Admin checks, and completion use one immutable registry
identity: the internal correction revision, normalized registrations, and complete
transitive dependency fingerprints (resolved paths, sizes and nanosecond mtimes).
References are rechecked before cache hits. Changed dependencies or `HYPERVIEW`
settings refresh all consumers without restarting Django.

The fixed trusted package overlay leaves upstream XSD artifacts unchanged.
`get_hyperview_catalog()` returns active `catalog_format: 2` without public
profile metadata. The config-free `get_hyperview_schema_path()` and
`build_hyperview_catalog()` helpers still inspect upstream resources, not the
active corrected runtime registry.

Follow [Add custom HXML elements](custom-schemas.md) for namespace registration
and the [custom component example](examples/hypertodo.xsd). Standard validation
does not require an additional install or callback.

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
    "ADMIN": {
        "EDITOR": True,
        "PERMISSION": "sample_app.permissions.can_edit_hyperview",
    },
}
```

Enabling the setting without the extra, or without `django_ace` in
`INSTALLED_APPS`, produces an actionable Django system-check error. When the
setting is false, the standard Django textarea remains unchanged.

Stored templates are executable Django template source, so mutations default to
superusers even when a staff user holds the model's ordinary add, change, or
delete permissions. A project can replace that policy with an inline callable:

```python
HYPERVIEW = {
    "ADMIN": {
        "PERMISSION": lambda request: request.user.is_superuser,
    },
}
```

For reusable and testable production configuration, prefer a dotted path:

```python
# sample_app/permissions.py
from django.http import HttpRequest


def can_edit_hyperview(request: HttpRequest) -> bool:
    """Return whether the current user may mutate stored HXML templates."""
    return request.user.has_perm("dj_hyperview_database.change_hyperviewtemplate")
```

```python
HYPERVIEW = {
    "ADMIN": {
        "PERMISSION": "sample_app.permissions.can_edit_hyperview",
    },
}
```

The callback is authoritative for all three mutation operations rather than an
additional Django model-permission check. Admin-site access still requires an
active staff user. Standard model view permission continues to govern read-only
access. Missing paths, non-callables, and callbacks that cannot accept one
request fail Django system checks; runtime exceptions and non-boolean return
values fail closed.

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

## Corrected schema boundaries

The internal correction revision is package-owned, not a user-selectable profile.
It accepts signed decimal percentages on the nine margin attributes while keeping
integer and `auto` values, and adds only these verified declarations:

| Element | Added attributes | Boundary |
| --- | --- | --- |
| `text` | `ellipsizeMode` | `clip`, `head`, `middle`, `tail`; `clip` is iOS-specific |
| `text`, `text-field` | `accessibilityLabel` | String, including empty text |
| `text` | `accessibilityRole` | `button`, `none` |
| `image` | `accessibilityRole`, `alt` | Role `button`; alternative text is a string |
| `text` | `importantForAccessibility` | `auto`, `yes`, `no`, `no-hide-descendants`; Android-specific |
| `date-field` | `cancel-label`, `done-label` | Strings consumed by the iOS modal, not Android's native picker |

The correction restricts only `style@width`, not the shared upstream sizing types. It accepts
nonnegative decimal points with a leading digit (`0`, `84`, `12.5`, `0.5`) and
decimal percentages (`0%`, `12.5%`, `.5%`, `150%`); a leading `+` is accepted.
It rejects negative values, `auto`, `.5` without `%`, trailing decimal points,
units, exponent notation, arbitrary suffixes, `NaN`, and `Infinity`.

These are deliberate lexical boundaries. Hyperview 0.110.0 truncates point
values with `parseInt`: `12.5` becomes `12`. Validation never rewrites the input.
React Native itself supports fractional dimensions and `auto`, but this pinned
Hyperview converter passes `auto` as `NaN`, not as the native auto value.

The corrected schema does **not** permit `accessibilityElementsHidden` as a boolean
string, move `styles` outside its owning `screen`, accept arbitrary native
properties, or establish native visual/accessibility parity. The evidence is
pinned client source plus package tests, not device E2E testing.

## Register typed schema extensions

`SCHEMA_EXTENSIONS` defaults to `{}`. Invalid registrations produce
`dj_hyperview.E021`. Registration describes server validation
and editor metadata, not executable mobile implementations.

```python
HYPERVIEW["SCHEMA_EXTENSIONS"] = {
    "BEHAVIORS": {
        "show-snackbar": {
            "ATTRIBUTES": {
                "message": {"TYPE": "string"},
                "tone": {"TYPE": "string", "ENUM": ["success", "error"]},
            },
        },
    },
    "ELEMENT_ATTRIBUTES": {
        "image": {
            "variant": {"TYPE": "string", "ENUM": ["face", "fingerprint"]},
        },
    },
}
```

A descriptor requires `TYPE`: `string`, `boolean`, `integer`, or `decimal`.
`REQUIRED` defaults to `False`; `True` requires the attribute's presence, not
nonempty string content. Optional `ENUM` is a nonempty sequence of unique XML
strings and is valid only with `TYPE: "string"`. There are no implicit defaults,
coercions or output mutations. XML boolean lexical forms are `true`, `false`,
`1`, and `0`; declaring a type does not convert a mobile client's raw attribute.

All sections and descriptor keys are closed. Names must be unqualified ASCII
XML names; names starting with `xml` are reserved. Standard actions and standard
attributes cannot be redefined. Qualified and unqualified names are distinct:
custom `message` can coexist with standard `alert:message`. `ELEMENT_ATTRIBUTES`
can add attributes only to existing Hyperview elements other than `behavior`.
Use `BEHAVIORS` for action-specific attributes; custom actions are supported on
`behavior`, not inline on `view`, `text`, or other built-in elements.

Strings can be empty, including an opaque revocation token. Register DOM target
identifiers as strings when they may reference a host outside the current
fragment; registration does not impose document-wide `xs:IDREF` existence.
See [custom schema contracts](custom-schemas.md#typed-registrations-and-registry-identity)
for validation, catalog and trust boundaries.
