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
        "MODE": "publish_and_render",
        "SCHEMA": "dj_hyperview.validate_hyperview_schema",
        "MAX_BYTES": 250_000,
        "MAX_DEPTH": 48,
        "MAX_NODES": 8_000,
    },
}
```

Publish validation rejects unsafe declarations before Django compiles a
template. Render validation then parses the final UTF-8 XML with entity
resolution and network access disabled, applying byte, depth, node, and schema
limits. DTD declarations and entities are forbidden. The optional bundled
validator uses XSD 1.1 and the official Hyperview 0.110.0 schemas.

Configured extra schemas may use local includes, imports, and redefines within
their own directory. Remote schema references and traversal outside that root
are rejected before compilation. Duplicate declarations must be identical.
The registry never fetches schemas from the network or exposes schema paths in
public errors. `MAX_DEPTH` cannot exceed the libxml2 safety ceiling of 256.
Compiled schemas are reused until their path, size, modification time, or the
relevant Django setting changes.

## Render a Hyperview CSRF field

Load the package tag library and render `hv_csrf_token` inside a form:

```xml
{% load dj_hyperview %}
<?xml version="1.0" encoding="UTF-8"?>
<view>
  <form action="/submit/" method="post">
    {% hv_csrf_token %}
  </form>
</view>
```

The tag emits an escaped hidden Hyperview text field. Render validation removes
leading whitespace produced before an XML declaration, and Hyperview responses
require UTF-8 in both the declaration and HTTP charset.

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
