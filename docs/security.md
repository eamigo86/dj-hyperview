# Name and XML security

Treat template names and render-context values as untrusted input. Template
source is executable Django template code, not untrusted data: installed tags,
filters, and objects exposed through the render context can access sensitive
application state. Grant publication and admin permissions only to people with
developer-level trust. XML validation limits the rendered document; it does not
sandbox Django template execution.

## Restrict database-template mutations

The database admin defaults to `request.user.is_superuser` for add, change, and
delete operations, even when a staff user holds ordinary model permissions.
Keep that default unless a narrower trusted maintainer group is required.

Configure `ADMIN.PERMISSION` with a callable or a dotted path such as
`sample_app.permissions.can_edit_hyperview`. The callback receives the current
request and is the authoritative mutation policy. It must return the literal
boolean `True`; import errors fail Django system checks, while runtime errors and
non-boolean values fail closed. Active staff status remains mandatory for access
to the Django admin itself.

## Use canonical names

Names are relative, case-preserving POSIX paths. Reject absolute paths, empty or
dot segments, backslashes, NUL bytes, control characters and isolated surrogates
rather than normalizing them. Filesystem sources also reject symlink
escapes. Public errors do not echo hostile names or absolute paths.

## Set explicit XML limits

Defaults are safe for general use; lower them for smaller documents:

```python
HYPERVIEW = {
    "VALIDATION": {
        "MAX_BYTES": 250_000,
        "MAX_DEPTH": 48,
        "MAX_NODES": 8_000,
    },
}
```

Publish validation rejects unsafe declarations before Django compiles a
template. Render validation then parses the final UTF-8 XML with entity
resolution and network access disabled, applying byte, depth, node, and schema
limits. DTD declarations and entities are forbidden. The bundled XSD 1.1
registry is always enforced, with one internal set of verified corrections.
`SCHEMA_PROFILE`, `VALIDATION.MODE`, and `VALIDATION.SCHEMA` are rejected, not
ignored. Resource limits remain configurable, but no setting disables validation.

Eager Hyperview responses and public body mutations validate before committing a
replacement; invalid mutations preserve the previous body. Lazy responses retain
Django's deferred rendering and validate their generated representation before
exposure. The integrated engine/response pipeline parses and checks XSD once;
independent public render and response calls may each validate their input.
Only a private immutable handoff reuses exact already-validated bytes and the
matching registry/limit identity. A SafeString is not a bypass.

Explicit plain Django `HttpResponse` objects, raw Django `engine.backend` calls,
and callbacks returning another Django response remain outside this guarantee.
Use a non-HXML error response when even a valid error document would exceed the
configured HXML limits. See [HTTP responses](http-responses.md) for HEAD and
no-content status rules.

Declaration scanning is linear in document length, including repeated unclosed
Django delimiters. Only the source scan recognizes Django comments: after
rendering, values such as `{% comment %}` are literal data, not template syntax.
XML comments, CDATA and processing instructions remain inert in both passes.
Source inline comments follow Django's LF-only boundary; malformed block
comments are left for template compilation to reject. Keep byte limits enabled
as a separate bound on memory and parser work.

Configured extra schemas may use local includes, imports, and redefines within
their own directory. Remote schema references and traversal outside that root
are rejected before compilation. References must use plain local paths:
percent-encoded locations are rejected so URL decoding cannot redirect the
compiler to a different dependency than the guard inspected. Each extra schema
is bounded to 256 distinct canonical files, with include cycles visited once.
`xs:override` is forbidden in extra schemas;
only the fixed, packaged compatibility adaptation uses it. Duplicate
declarations must be identical.
The registry never fetches schemas from the network or exposes schema paths in
public errors. `MAX_DEPTH` cannot exceed the libxml2 safety ceiling of 256.
Validation and editor catalogs check the complete transitive dependency graph
before reusing cached results. Changes to dependency paths, sizes, modification
times, or settings invalidate the shared identity, so completion cannot retain
a stale included schema while validation uses its replacement.

## Render a Hyperview CSRF field

Load the package tag library and render `hv_csrf_token` inside a form:

```xml
{% load dj_hyperview %}
<?xml version="1.0" encoding="UTF-8"?>
<view xmlns="https://hyperview.org/hyperview">
  <form>
    {% hv_csrf_token %}
    <view>
      <behavior trigger="press" action="replace" href="/submit/" verb="post" />
      <text>Submit</text>
    </view>
  </form>
</view>
```

The tag emits an escaped hidden Hyperview text field. Render validation removes
leading whitespace produced before an XML declaration, and Hyperview responses
require UTF-8 in both the declaration and HTTP charset.
An initial UTF-8 BOM is preserved, but cannot hide a contradictory encoding
declaration such as `ISO-8859-1`.

Use Django template variables for dynamic values so autoescaping remains active;
do not concatenate untrusted text into XML markup. `TemplateValidationError`
uses stable public codes and messages. Log application context separately rather
than exposing template content, rejected names, schema paths, or chained parser
details to clients.

The dedicated Hyperview engine inherits only consumer `context_processors`,
`string_if_invalid`, `builtins`, and `libraries` options. Security-sensitive
engine options such as `autoescape` are intentionally isolated, even when the
consumer backend subclasses Django's `DjangoTemplates` backend.

Call the public validator when accepting content outside the engine:

```python
from dj_hyperview import validate_hxml

validated = validate_hxml(candidate_document)
```
