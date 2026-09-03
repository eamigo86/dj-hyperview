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
`TemplateNotFound`.

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
Cached JSON is treated as untrusted: exact version/shape/type and lookup identity
must match, while duplicate keys or invalid aliases raise `SourceUnavailable`.
Checks and runtime accept an alias only when Django resolves its configured name
to a cache backend; leading underscores alone do not make an alias invalid.

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
