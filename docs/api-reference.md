# API Reference

Import stable public objects from `dj_hyperview`. Modules and names beginning
with an underscore remain implementation details.

| Symbol | Purpose |
| --- | --- |
| `__version__` | Installed `dj-hyperview` distribution version. |

## Resolution and sources

| Symbol | Purpose |
| --- | --- |
| `TemplateResolver` | Resolve canonical names through ordered sources. |
| `resolve_template` | Resolve one name using current Django settings. |
| `TemplateSource` | Contract implemented by template sources. |
| `FileSystemSource` | Read project-owned files from configured roots. |
| `ResolvedTemplate` | Immutable result returned by a source. |
| `ResolverLoader` | Connect the resolver to Django's template engine. |

## Rendering and HTTP

| Symbol | Purpose |
| --- | --- |
| `HyperviewEngine` | Compile, render, and validate consumer templates. |
| `render_template` | Render one template using current settings. |
| `HyperviewResponse` | Return explicit Hyperview markup. |
| `HyperviewTemplateResponse` | Preserve Django's lazy template response. |
| `HyperviewFragmentResponse` | Return and validate one eager bare fragment. |
| `HyperviewFragmentTemplateResponse` | Lazily render and validate one bare fragment. |
| `HyperviewTemplateView` | Serve a named template from a class-based view. |
| `HYPERVIEW_MEDIA_TYPE` | Canonical Hyperview response media type. |
| `HYPERVIEW_FRAGMENT_MEDIA_TYPE` | Canonical replacement-fragment media type. |

## Request detection

| Symbol | Purpose |
| --- | --- |
| `HyperviewMiddleware` | Attach typed client details to the request. |
| `HyperviewRequestDetails` | Represent detected client metadata. |
| `detect_hyperview_request` | Detect a Hyperview request explicitly. |
| `HYPERVIEW_VERSION_HEADER` | Header used for client version metadata. |

## Validation and cache

| Symbol | Purpose |
| --- | --- |
| `validate_template_source` | Validate raw template source safety before compilation. |
| `validate_hxml` | Validate a final rendered HXML document against configured limits and schema. |
| `validate_fragment_hxml` | Validate fragment XML, limits, and client-safe root shape. |
| `TemplateCache` | Cache raw resolved templates when enabled. |
| `CacheEntry` | Represent a cache hit or negative entry. |
| `CACHE_MISS` | Represent an explicit cached source miss. A cache lookup returns `None` when no cache entry exists. |
| `template_cache_key` | Produce a namespaced cache key. |
| `invalidate_templates` | Invalidate one or more canonical names. |

## Exceptions

`HyperviewError` is the package base exception. Its public specializations are
`HyperviewConfigurationError`, `InvalidTemplateName`, `TemplateNotFound`,
`TemplateValidationError`, and `SourceUnavailable`.

Database publication is an optional contribution with its own service boundary.
See [Database source and admin](database-admin.md) for those imports.
