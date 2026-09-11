# API Reference

[Step-by-step SSE guide](realtime.md): setup, negotiated changes, compatibility examples and host responsibilities.

Import documented public objects from `dj_hyperview`; catalog helpers are in
`dj_hyperview.schema`. Modules and names beginning
with an underscore remain implementation details.

| Symbol | Purpose |
| --- | --- |
| `__version__` | Installed `dj-hyperview` distribution version. |

## Beta compatibility policy

**Use the documented public APIs; test your integration before upgrading.**
**0.1.0b1 starts the 0.1 beta line.** This is a prerelease for integration
feedback, not a blanket production-readiness guarantee. Verify the published
version and its release checks before adopting a source candidate.

Within the 0.1 beta line, maintainers will preserve the documented public Python
imports and call contracts, configuration keys, source protocol, HTTP media
types, and versioned schema/SSE contracts. Additive APIs may be introduced;
existing documented integrations should not require changes simply to upgrade
to the next beta in the same line. Supported Python/Django removals also count
as compatibility changes. The [verified scope](testing.md#verified-scope) limits
what has actually been tested; it is not a guarantee for every deployment.

If an incompatible change is necessary, the same release must:

1. Mark it explicitly as a **Breaking change** in the [changelog](changelog.md).
2. Explain the affected APIs/clients, migration steps and rollback constraints.
3. Include regression tests for the replacement and the retained compatibility
   path, or for an explicit rejection when that old path cannot remain safe.

Security fixes may require a faster incompatible change without a normal
advance deprecation period. They still require a documented security rationale,
upgrade guidance and regression tests; unsafe behavior is not preserved merely
to keep an old integration working.

This commitment covers the surfaces documented here and in the linked
[configuration](configuration.md), [database services](database-admin.md),
[schema extensions](custom-schemas.md) and [HTTP](http-responses.md) guides.
Underscore-prefixed internals, cache serialization, Admin DOM/editor internals
and test helpers are not extension APIs unless explicitly documented as such.
A template `revision` is a concurrency/cache identity, not a stored history or
an application-version selector.

The consumer still owns authentication, authorization, CSRF, business data,
routes, native components and capability negotiation. An untrusted client
version or mutation marker never grants permissions. SSE v2 requires compatible
backend readers and an adapted client; preserve v1 projection during a
coordinated rollout or rollback. See the [SSE guide](realtime.md).

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
| `HyperviewResponse` | Validate and return explicit Hyperview markup. |
| `HyperviewTemplateResponse` | Preserve Django's lazy template response. |
| `HyperviewFragmentResponse` | Return and validate one eager bare fragment. |
| `HyperviewFragmentTemplateResponse` | Lazily render and validate one bare fragment. |
| `HyperviewTemplateView` | Serve a named template from a class-based view. |
| `HYPERVIEW_MEDIA_TYPE` | Canonical Hyperview response media type. |
| `HYPERVIEW_FRAGMENT_MEDIA_TYPE` | Canonical replacement-fragment media type. |

All package render methods, including compiled templates from `get_template()`
and `select_template()`, validate final output automatically. `engine.backend`
is the underlying low-level Django backend, not a validated rendering API.

HTTP body construction, assignment, `write()`, and `writelines()` validate the
complete candidate before changing stored bytes. See [HTTP responses](http-responses.md)
for bodyless statuses, HEAD request context, and lazy callback semantics.

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
| `validate_hxml` | Validate a final rendered HXML document against resource limits and the automatic corrected schema. |
| `validate_fragment_hxml` | Validate fragment XML, limits, the automatic schema, and client-safe root shape. |
| `validate_hyperview_schema` | Validate rendered HXML with the automatic corrected XSD 1.1 registry. |
| `HYPERVIEW_SCHEMA_VERSION` | Bundled upstream Hyperview schema release. |
| `HYPERVIEW_VALIDATION_CONTRACT` | Capability identifier `automatic-xsd-v1`. |
| `schema.get_hyperview_catalog` | Inspect the corrected runtime declarations and registered extensions (catalog format 2). |
| `schema.build_hyperview_catalog` | Inspect frozen upstream declarations without selecting a runtime schema. |
| `TemplateCache` | Cache raw resolved templates when enabled. |
| `CacheEntry` | Represent a cache hit or negative entry. |
| `CACHE_MISS` | Represent an explicit cached source miss. A cache lookup returns `None` when no cache entry exists. |
| `template_cache_key` | Produce a namespaced cache key. |
| `invalidate_templates` | Invalidate one or more canonical names. |

## Committed template invalidations

`TemplateInvalidation` and `template_invalidated` are public in `dj_hyperview`
and `dj_hyperview.signals`. The frozen event has `names: frozenset[str]` and
`using: str`; construction validates canonical names and snapshots mutable inputs.

```python
from django.dispatch import receiver
from dj_hyperview.signals import template_invalidated


@receiver(template_invalidated)
def templates_changed(sender, event, **kwargs):
    # Map event.names and event.using to application-owned invalidation policy.
    pass
```

The optional database contribution sends `sender=HyperviewTemplate, event=event`
after commit on the mutation alias. Model saves/deletes, publication services,
controlled QuerySet updates/deletes and bulk operations share the existing
invalidation scheduler. Renames include old and new names. Empty/irrelevant writes
and rollbacks emit nothing; conflict-ignored inserts can conservatively include
names that did not change. Multiple callbacks in a transaction are not coalesced.

Cache invalidation runs first; `send_robust` runs in the same callback's `finally`,
even with caching disabled or a cache exception. Receiver exceptions are isolated
by Django; the original cache exception still propagates after the database commit.
These are best-effort hints, not durable delivery or proof that content changed.
Earlier failing commit callbacks can prevent later callbacks, as in Django itself.
No Redis, watcher, filesystem-deploy hook or network transport is enabled.
Calling `invalidate_templates()` directly still only invalidates cache.

## Exceptions

`HyperviewError` is the package base exception. Its public specializations are
`HyperviewConfigurationError`, `InvalidTemplateName`, `TemplateNotFound`,
`TemplateValidationError`, and `SourceUnavailable`.

`TemplateValidationError` exposes the stable `code` and `message` fields plus
optional one-based `line` and `column` coordinates. Its string form never
includes rejected source content or internal filesystem paths.

Database publication is an optional contribution with its own service boundary.
See [Database source and admin](database-admin.md) for those imports.

## Realtime transport (optional)

`from dj_hyperview.conf import RealtimeSettings, get_settings` exposes the
normalized `HYPERVIEW["REALTIME"]` configuration. `get_settings().realtime` is
`None` when omitted/disabled, or frozen `RealtimeSettings(redis_url, namespace)`.
The URL is excluded from repr. An enabled mapping requires exactly `REDIS_URL`
and `NAMESPACE`; invalid settings produce check `dj_hyperview.E022` and raise
`HyperviewConfigurationError`, not a disabled fallback. `REALTIME.ALIAS` is not
supported. Reading these settings neither imports redis-py nor connects.

Import from `dj_hyperview.realtime`; none of these symbols connects at import:

- `INVALIDATION_VERSIONS == (1, 2)` lists supported invalidate schemas as an immutable tuple;
  it does not enable client negotiation automatically.
- `RedisBroker(url, namespace)` validates immutable server configuration.
- `broker.publish_after_commit(event, topics, *, using)` captures input and
  schedules a bounded, best-effort committed hint; return value is `None`, not a
  delivery acknowledgement. Invalid inputs raise `ValueError` before scheduling.
- `await broker.subscribe(topics)` returns the `Subscription` protocol only
  after all ACKs; its first envelope is `resync`. Transport/setup failures raise
  `RealtimeUnavailable` with a fixed message. `async for` consumes closed
  envelopes; `await subscription.aclose()` closes ownership once.
- `realtime_asgi(application)` wraps the outer Django ASGI application once;
  this owns request cleanup across synchronous middleware adapters.
- `sse_response(async_events, *, aclose)` transfers an iterator and explicit
  owner inside that active scope. `None` emits a heartbeat comment.

Envelope fields, framing and cooperative cleanup requirements are in
[HTTP responses](http-responses.md#owned-asynchronous-sse-responses).
[Broker configuration](configuration.md#optional-realtime-broker) defines URL,
namespace, topic, queue and deadline limits. None of these APIs selects users,
grants authorization, transports HXML or acknowledges a mobile layout commit.
