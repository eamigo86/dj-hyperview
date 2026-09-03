# dj-hyperview

Reusable Django infrastructure for serving Hyperview UI supplied by the host
project.

## Install for local development

```bash
uv sync --all-groups
uv run pytest
```

The package will resolve templates provided by a consumer from configured XML
directories or, when enabled, from an optional Django database application.

## Scope

`dj-hyperview` provides resolution, rendering, validation, caching, and Django
integration. It does **not** ship application screens, runtime XML/HXML files,
mobile components, Redis, or database/admin requirements. XML/HXML fixtures are
kept under `tests/` and excluded from the published package.

## HTTP contract

Responses default to Hyperview's vendor media type,
`application/vnd.hyperview+xml`, while accepting Django's explicit
`content_type`, status, charset, and headers arguments. Consumer projects supply
the markup directly or through their own templates:

```python
from dj_hyperview import HyperviewResponse, HyperviewTemplateView


def screen(request):
    return HyperviewResponse("<view>Ready</view>", status=200)


class DetailScreen(HyperviewTemplateView):
    template_name = "mobile/detail.xml"  # Provided by the consumer project.
```

Template responses remain unrendered until Django renders them, preserving the
standard `TemplateResponse` lifecycle.

## Request integration

Add the middleware to attach typed metadata without changing the response:

```python
MIDDLEWARE = [
    "dj_hyperview.middleware.HyperviewMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]
```

`request.hyperview` is truthy for the client's optional
`X-Hyperview-Version` header or an explicit Hyperview vendor media type in
`Accept`; its `version` is `None` when detection came only from negotiation.
Generic `application/xml` and wildcard requests are not classified as
Hyperview.

Mutable Hyperview forms can use Django's normal CSRF protection:

```django
{% load dj_hyperview %}
<form>
  {% hv_csrf_token %}
</form>
```

The tag emits an XML-escaped hidden `csrfmiddlewaretoken` field. It calls
Django's standard token API and does not bypass `CsrfViewMiddleware`.

## Template sources

Sources are queried in declaration order; the first match wins. The built-in
filesystem source reads UTF-8 templates from the consumer's directories:

```python
HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "mobile_screens"],
    "SOURCES": [
        {"BACKEND": "dj_hyperview.sources.FileSystemSource"},
        {
            "BACKEND": "my_project.hyperview.TenantSource",
            "OPTIONS": {"tenant_key": "slug"},
        },
    ],
}
```

```python
from dj_hyperview import render_template, resolve_template

screen = resolve_template("account/profile.xml")
markup = render_template("account/profile.xml", {"username": "Ada"})
```

Template names are relative POSIX paths. Absolute paths, empty or dot segments,
backslashes, NUL bytes, and filesystem symlink escapes are rejected. A source
returns `None` only for a miss; when every source misses, resolution raises
`TemplateNotFound`. Unicode remains case-preserving and unnormalized; combining
marks and format characters are accepted, while control and surrogate code
points are rejected.

### Optional database app

Install `"dj_hyperview.contrib.database"` in `INSTALLED_APPS` and run Django
migrations to make the `HyperviewTemplate` model available. Then enable the
database source where its precedence belongs:

```python
INSTALLED_APPS = [
    # ...
    "dj_hyperview.contrib.database",
]

HYPERVIEW = {
    "SOURCES": [
        {"BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource"},
    ],
}
```

The source uses Django's default manager and database router. Router-selected
sources are intentionally uncached because routing may vary by request or tenant.
Set `"OPTIONS": {"using": "replica"}` to pin a configured database alias and
enable generic raw-cache acceleration with an alias-specific identity.
Active exact-name rows are hits, including empty content; inactive or absent
rows are misses and resolution continues to the next source. Without cache, a
later lookup observes row content and revision changes. Explicit aliases retain
normal cache TTL and manual invalidation semantics.

The base package does not import the model or require a database table. Model
`full_clean()` checks canonical names and template safety; `save()` intentionally
follows Django's standard behavior and does not call `full_clean()` automatically.
Installed mutation signals still reject a noncanonical name before its SQL write.
When `django.contrib.admin` is installed, Django autodiscovery registers a
standard `HyperviewTemplate` admin. Its create/edit forms reuse the model field
validators, expose content and active state, and keep revision and timestamps
read-only. Projects that omit Django admin do not import or register this module.

Model save/delete and `QuerySet.delete()` schedule invalidation with Django's
`transaction.on_commit()` on the mutation database alias. Renames invalidate the
old and new canonical names; rollbacks and rolled-back savepoints discard their
callbacks. A cache failure remains observable after commit and therefore does
not mean the database write rolled back. Raw fixture saves, `QuerySet.update()`
and bulk APIs still require explicit invalidation until their controlled Task 8
integrations are enabled.
Name uniqueness follows the database backend's collation: the package does not
case-fold names, and SQLite's default treats `screen.xml` and `Screen.xml` as
distinct.

## Cache contract

`TemplateCache` stores only serialized raw `ResolvedTemplate` data through a
configured Django cache alias; it never caches compiled templates. A lookup
returns `None` when no entry exists, `CACHE_MISS` for a cached source miss, and
returns a `CacheEntry` for content—even when that content is empty. Every hit or
miss is bound to its source, name, and revision. Backend failures use a stable
public error without exposing cached template data. Fixed-length
SHA-256 keys isolate each namespace. A non-empty `HYPERVIEW.CACHE` is opt-in;
omitting it or using `{}` never initializes a cache backend. Source identities hash
position plus effective backend options (including filesystem roots) without
exposing secrets; an unrepresentable custom configuration stays uncached.
Fingerprinting accepts a closed domain: exact built-in scalars, `dict`, `list`,
`tuple`, standard `pathlib` paths, and verified importable callables. Subclasses,
generic container implementations, ranges, mutable/buffer containers such as
`bytearray`, `memoryview`, and `array.array` stay uncached instead of risking an
identity that omits observable semantics.
Cached JSON is treated as untrusted: exact version/shape/type and lookup identity
must match, while duplicate keys or invalid aliases raise `SourceUnavailable`.
Checks and runtime accept an alias only when Django resolves its configured name
to a cache backend; leading underscores alone do not make an alias invalid.

Call `invalidate_templates("screens/home.xml")` after publishing raw content.
Invalidation rotates a shared, namespaced token for each canonical name, so all
sources' older hits and misses become invisible and a read that started earlier
cannot repopulate the new generation. Already-running renders may still finish
with their pinned snapshot. The call returns only after every requested token
rotation succeeds; any cache failure raises `SourceUnavailable`, independently
of the resolver's ordinary `bypass` policy. Multi-name rotation is not atomic,
so a partial failure is observable and callers may safely retry every name.
Every successfully claimed root or successor token leaves a shared,
non-expiring tombstone. Claim metadata binds each token to its root/successor
lifecycle, so repeated entropy cannot reuse an older generation even after raw
TTLs pass. Failed and concurrent candidates also remain claimed: correctness
costs roughly one small marker per generated candidate, reclaimed only with the
cache namespace/backend lifecycle.
Successors are domain-separated digests of the previous token plus fresh
entropy, not the entropy itself. Every raw content or miss write rechecks the
shared generation and removes a superseded exact key only when the backend
returns exactly `True`; `None`, `False`, and exceptions are ambiguous failures.
`bypass` may still return authoritative source data, but never reports stale
cache publication as successful or weakens its tombstone. If an operator evicts
a tombstone while retaining raw entries, generic caches cannot prove uniqueness;
cryptographic uniqueness is the fallback, not a durable transaction.

## Template engine

`render_template()` uses a dedicated Django template engine backed only by the
configured Hyperview sources. Root templates, `{% include %}`, and
`{% extends %}` therefore use the same canonical names and source precedence;
the host project's HTML template loaders are not modified. No compiled-template
cache is installed, so a new render sees newly published source content.
Templates returned by `get_template()` or `select_template()` preserve
Django's render signature and metadata while enforcing the same validation.

During one render, the first result—or miss—for each template name is pinned per
resolver identity. Nested engines sharing a resolver reuse its snapshot, while
different resolvers remain isolated even when they render the same name.
Repeated includes cannot mix revisions if a source changes concurrently, while
separate sync or async request contexts remain isolated. Dynamic names that have
not yet been resolved still observe source state at their first lookup because
the source protocol intentionally provides point lookups rather than a global
transaction.

When `HYPERVIEW["SOURCES"]` is configured, `HyperviewTemplateResponse` and
`HyperviewTemplateView` use this engine while preserving Django's lazy response,
status, header, context, and escaping behavior. An explicit `using=` continues
to select the consumer's standard Django template engine.

## HXML validation

Every public engine/response render path escapes context, rejects active
DTD/entities before compile without misclassifying comments or CDATA, and
enforces rendered XML schema, byte, depth, and node limits. Consumer XSD
includes/imports are denied to prevent network or traversal access. Failures
raise `TemplateValidationError`; no application schema or screen is included.
