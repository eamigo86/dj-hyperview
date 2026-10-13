# Name and XML security

`template_invalidated` is an in-process invalidation hint, not authorization.
Its canonical names and database alias must not be forwarded to clients as
trusted topics. Consumers own tenant/session checks and topic mapping; never
publish template contents or assume a hint proves a user-visible change.

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
dot segments, backslashes, NUL bytes, control characters, isolated surrogates,
invisible format characters, and line or paragraph separators rather than
normalizing them. Format characters (Unicode category Cf) include bidirectional
overrides, zero-width characters, and tag characters; they can make two names look
identical or hide an extension in logs and error pages. Filesystem sources also
reject symlink escapes. Public errors do not echo hostile names or absolute
paths. An ordered candidate list skips unsafe names, and its final
`TemplateNotFound` lists only the valid names that were not found. Passing one
string where a list is expected raises `TypeError`.

## Set explicit XML limits

Defaults are safe for general use; lower them for untrusted input or smaller
documents:

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

Three further limits are fixed and cannot be configured. They bound one element
and the path from the root to it, not the document: an element may declare at most
128 attributes and at most 8 in-scope namespaces, and the elements on the path
from the root to any element may carry at most 32 namespace declarations in
total. The default namespace and bindings inherited from ancestors count toward
the 8, and rebinding a prefix counts once there. Toward the 32 on a path, every
declaration counts, so rebinding the same prefixes at each level adds up. The
schema validator rebuilds the namespace map of every element from all of its
ancestors several times, and once more for each in-scope prefix when the element
declares a namespace. Its cost therefore grows with the declarations on a path
times the prefixes in scope, and `MAX_BYTES`, `MAX_DEPTH`, and `MAX_NODES` alone do
not bound it. An element over any cap fails before schema validation with the
stable code `max_attributes`. The message says which cap was exceeded and never
repeats attribute or prefix names. The widest element the bundled schema declares
has 126 attributes. The HyperTodo templates, with their includes expanded, use at
most 13 attributes on an element, 5 namespace declarations on a path, and 2
prefixes in scope, so valid documents are unaffected. That includes a screen
composed of many fragments that each declare the default namespace and an
application prefix: the declarations of a finished element leave the path, so
siblings do not accumulate. Extension schemas share the same caps.

Checking the caps takes time proportional to the number of nodes and declarations.
They keep one document from costing far more than its size; they do not make
validation cheap. Measured at the default limits with Python 3.12, lxml 6.1, and
xmlschema 4.3 on an Apple M3 Max machine, a plain valid document of 20,000
elements validates in about 0.6 s, and one of 19,000 elements with seven attributes
each, close to the default byte limit, in about 1.5 s. The most expensive document
found within the namespace caps, 20,000 elements that each rebind a prefix below a
path already at the cap, takes about 1.1 s, or 1.2 s with `MAX_DEPTH` at its ceiling
of 256. A rejected document stops at its first schema error, so rejecting it costs
about as much as validating the part before that error. Cost grows roughly in
proportion to the nodes and attributes, and your hardware may be slower, so plan for
one to two seconds of CPU per large untrusted document at the defaults, and measure
on yours. `MAX_BYTES`, `MAX_NODES`, and `MAX_DEPTH` remain the global bounds: lower
them for untrusted input.

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
percent-encoded locations, control or other non-printable characters, the
characters `?`, `#` and `:`, and any `..` segment are rejected so URL decoding,
the dropping of tabs and carriage returns, the dropping of an empty query or
fragment, drive-letter parsing, and lexical path folding cannot redirect the
compiler to a different dependency than the guard inspected. For the plain paths
it accepts, the guard reads a reference from the location of the file that
contains it, as the compiler does, so a symlink cannot pull a dependency from
outside the root. Each extra schema is bounded to 256 distinct file paths, with
include cycles visited once.
`xs:override` is forbidden in extra schemas;
only the fixed, packaged compatibility adaptation uses it. Duplicate
declarations must be identical.
The registry never fetches schemas from the network or exposes schema paths in
public errors. `MAX_DEPTH` cannot exceed the libxml2 safety ceiling of 256.
Validation and editor catalogs check the complete transitive dependency graph
before reusing cached results. Changes to dependency paths, sizes, modification
times, or settings invalidate the shared identity, so completion cannot retain
a stale included schema while validation uses its replacement.

## Keep user-supplied text valid XML

XML 1.0 cannot represent every character. It allows tab, line feed, carriage
return, and characters from U+0020 upward, except lone surrogates, U+FFFE, and
U+FFFF. Django autoescaping covers markup characters, not these. A context value
such as `"Buy\x01milk"` that reaches the rendered document makes the whole
document invalid. Validation fails closed with `TemplateValidationError` code
`malformed_xml` and the fixed message "document contains characters not allowed
in XML 1.0". It repairs nothing and names neither the character nor its position.
Eager responses and `write()` give the same message for a lone surrogate. A
document that also exceeds `MAX_BYTES`, declares a DTD or entity, or declares a
non-UTF-8 encoding reports `max_bytes`, `forbidden_declaration`, or
`invalid_encoding` instead, because those checks run first. A lone surrogate is
the exception to the first two: the guard encodes the document before it measures
or scans it, so the validators report `malformed_xml` for a document that also
exceeds `MAX_BYTES` or declares a DTD or entity. An eager response compares its
character count with `MAX_BYTES` before it encodes the text, so it still reports
`max_bytes` when the text is already longer than the limit. An XML declaration
that names a non-UTF-8 encoding, or a name that no codec can look up, such as one
containing NUL, still reports `invalid_encoding` first.

Reject the text where you accept it, or neutralize it where you render it:

- `dj_hyperview.validators.validate_xml_text` is a Django validator for model and
  form fields. It raises `ValidationError` with the code `xml_characters` and
  does not repeat the rejected value.
- The `hv_text` filter removes those characters and escapes the rest for element
  content. The `hv_attr` filter does the same for an attribute value and also
  encodes line feeds, carriage returns, and tabs as character references; XML
  parsers otherwise normalize them to spaces.

```python
from django.db import models

from dj_hyperview.validators import validate_xml_text


class Task(models.Model):
    title = models.CharField(max_length=200, validators=[validate_xml_text])
```

```xml
{% load dj_hyperview %}
<?xml version="1.0" encoding="UTF-8"?>
<view xmlns="https://hyperview.org/hyperview">
  <text-field name="title" value="{{ task.title|hv_attr }}" />
  <text>{{ task.title|hv_text }}</text>
</view>
```

The filters return escaped markup, so Django does not escape their output a
second time. They silently drop illegal characters, so prefer the validator when
dropping data is not acceptable. Keep autoescape enabled and do not mark
untrusted text safe yourself.

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
require UTF-8 in both the declaration and HTTP charset. Only XML whitespace
(space, tab, carriage return, and line feed) is removed; any other leading
character, such as a control character or a non-breaking space, is reported as an
error rather than stripped.
An initial UTF-8 BOM is preserved and whitespace after it is removed, but it
cannot hide a contradictory encoding declaration such as `ISO-8859-1`.

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

### Realtime boundaries

Store server-owned transport configuration under `HYPERVIEW["REALTIME"]`, using
exactly `REDIS_URL` and `NAMESPACE`. Supply credentials through your deployment's
secret handling, never request/XML data. Reading settings performs no Redis I/O;
invalid values fail closed with sanitized `dj_hyperview.E022`, rather than
silently disabling realtime. Normalized `RealtimeSettings` excludes the URL
from repr; that is not permission to log the raw Django settings mapping.

Authenticate before opening SSE and recheck persisted authority before each
hint/heartbeat. Select topics only from server-owned identity; never forward
client topic names. The generic package does not implement authorization,
connection quotas or app resource policy. Use a distinct app/environment
namespace, TLS for remote Redis, and Redis ACLs permitting only the required
Pub/Sub commands. No scripting permission is required. A database index does
not isolate Redis Pub/Sub channels.

Only closed logical envelopes cross the transport, never HXML, template names,
user identifiers, URLs, credentials or request bodies. Limit app resource names
further than the generic ASCII grammar. Logs use fixed codes without Redis URL,
topic or payload. A failed publication adds only the exception type name, as in
`error_type=<TypeName>`, never its message, which can contain the Redis URL.
`realtime_asgi` owns cleanup for pre-iteration disconnects and adapted views;
release admission in the explicit cleanup callback's `finally`. Do not interpret
a timeout, resync or broker publish return as successful delivery or completed
client rendering.
