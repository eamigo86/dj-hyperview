# Name and XML security

Treat template names and render-context values as untrusted input. Template
source is executable Django template code, not untrusted data: installed tags,
filters, and objects exposed through the render context can access sensitive
application state. Grant publication and admin permissions only to people with
developer-level trust. XML validation limits the rendered document; it does not
sandbox Django template execution.

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
        "SCHEMA": BASE_DIR / "schemas" / "hyperview.xsd",
        "MAX_BYTES": 250_000,
        "MAX_DEPTH": 48,
        "MAX_NODES": 8_000,
    },
}
```

Publish validation rejects unsafe declarations before Django compiles a
template. Render validation then parses the final UTF-8 XML with entity
resolution and network access disabled, applying byte, depth, node, and schema
limits. DTD declarations and entities are forbidden. XSD includes, imports, and redefines
are forbidden because they could cross the local schema boundary.

Use Django template variables for dynamic values so autoescaping remains active;
do not concatenate untrusted text into XML markup. `TemplateValidationError`
uses stable public codes and messages. Log application context separately rather
than exposing template content, rejected names, schema paths, or chained parser
details to clients.

Call the public validator when accepting content outside the engine:

```python
from dj_hyperview import validate_hxml

validated = validate_hxml(candidate_document)
```
