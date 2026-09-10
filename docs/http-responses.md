# Return Hyperview documents and fragments

Use a full-document response for navigation and a fragment response for an
in-place update. dj-hyperview applies the correct media type and validates the
final XML against the bundled corrected schema before bytes reach the client.
No schema installation extra, callback, or activation setting is required.

## Return a complete screen

Use `HyperviewTemplateResponse` when the endpoint renders a consumer-owned
template:

```python
from django.http import HttpRequest

from dj_hyperview import HyperviewTemplateResponse


def home(request: HttpRequest) -> HyperviewTemplateResponse:
    return HyperviewTemplateResponse(request, "screens/home.xml")
```

The response uses `application/vnd.hyperview+xml`. The rendered result must be
a valid complete HXML document.

## Return an update fragment

Hyperview `replace`, `append`, and `prepend` actions expect
`application/vnd.hyperview_fragment+xml` and one bare element:

```python
from django.http import HttpRequest

from dj_hyperview import HyperviewFragmentTemplateResponse


def task_row(request: HttpRequest) -> HyperviewFragmentTemplateResponse:
    return HyperviewFragmentTemplateResponse(
        request,
        "fragments/task-row.xml",
        {"task_id": request.POST["task_id"]},
    )
```

Use `HyperviewFragmentResponse` when markup is already available:

```python
from dj_hyperview import HyperviewFragmentResponse


response = HyperviewFragmentResponse(
    '<view xmlns="https://hyperview.org/hyperview" id="saved"><text>Saved</text></view>'
)
```

A fragment must not be wrapped by `doc`, `navigator`, `screen`, or `body`.
Those roots are valid only in their full-document context and raise
`TemplateValidationError` when returned as a fragment.

## Validation and atomic body changes

All four response classes validate the final XML, resource limits, and corrected
schema automatically. Fragment classes also enforce the client-safe root
restriction. A custom `content_type` does not disable validation. Content and
XML declarations must use UTF-8; an incompatible response charset raises
`ValueError`.

Constructors and later `content` assignment validate the complete candidate
before replacing the body. `write()` appends to the existing document;
`writelines()` assembles all chunks once and validates the combined result.
Neither operation is an XML streaming API. Invalid XML, schema errors, encoding
errors, or exceeded limits leave previously committed bytes unchanged.

An empty response is allowed only with status **204, 205, or 304**. Those statuses
reject nonempty content, including whitespace. `HyperviewResponse()` with its
default 200 status therefore raises `TemplateValidationError`. Changing a status
to conflict with the stored body is rejected when content is read or emitted.
These cheap checks do not repeat XML parsing or XSD validation.

Bodyless statuses may retain representation metadata such as `ETag` and
`Content-Encoding` without a payload. This does not permit nonempty content.

## Lazy rendering and HEAD requests

Template responses remain unrendered until `render()`. Invalid output raises
before commitment and leaves the response unrendered; accessing its body still
raises Django's `ContentNotRenderedError`. A successful repeated `render()` is a
no-op. `rendered_content` returns fresh validated text without committing it.
For 204, 205, and 304, the template is still rendered once and must return exactly
an empty string; content is never silently discarded.

Django post-render callbacks keep their normal order and replacement semantics.
Mutations to a Hyperview response remain validated. A callback may instead return
an ordinary Django response, which is outside the Hyperview validation guarantee.

HEAD requests validate the same nonempty representation as GET before transport
suppression. Lazy responses already receive the request. For eager responses,
pass it explicitly so Django can suppress HEAD transport without replacing the
validated document with empty XML:

```python
def saved(request):
    return HyperviewFragmentResponse(
        '<text xmlns="https://hyperview.org/hyperview">Saved</text>',
        request=request,
    )
```

This context does not permit an empty 200 constructor or invalid mutations. The
validated representation and its headers, including a supplied `Content-Length`,
are retained when HEAD transport is suppressed. Without HEAD request context,
an empty 200 body assignment remains an error.

If Hyperview reports `XMLRestrictedElementFound`, inspect the initiating action,
the response media type, and the first response element together. For an
in-place action, return only the element that replaces or extends the target.


## Gzip transport middleware

Django's `GZipMiddleware` remains supported after the document is validated.
The response keeps its immutable validated representation separately from gzip
transport bytes. It accepts a gzip reassignment only when bounded decompression
produces that exact representation: truncated, trailing, concatenated, oversized,
or different content is rejected. A gzip constructor is not a validation API.

Django briefly reads the compressed body before setting its encoding header;
final iteration and serialization require `Content-Encoding: gzip`. HEAD can
then suppress transport without losing the validated representation. This does
not add another XML parse or XSD pass unless the validation contract has changed.

To replace or append XML after compression, first remove `Content-Encoding`.
The normal mutation then validates a complete candidate before atomically
clearing gzip transport and its old `Content-Length`. Invalid updates leave the
previous representation and transport bytes intact. `response.text` decodes
transport bytes, matching Django; it does not transparently decompress gzip.

## Owned asynchronous SSE responses

Use `dj_hyperview.realtime.sse_response(async_events, *, aclose)` for transport
hints, **not HXML or successful-refresh acknowledgements**. It returns Django's
`StreamingHttpResponse`, with `text/event-stream`, `Cache-Control: no-cache,
no-transform` and `X-Accel-Buffering: no`. Redis is not required to import or use
this response adapter.

Wrap the outer ASGI application once with
`application = realtime_asgi(get_asgi_application())`, importing the wrapper
from `dj_hyperview.realtime`. Construct the response inside that active HTTP
scope, on the subscription's live event loop. Django sync-only middleware is
supported: an adapted view task ending does not end the HTTP request. Outside
an active scope construction fails before ownership transfers. The mandatory
asynchronous `aclose` callback owns the
subscription and admission permit immediately, before the first iteration.
Release admission in `finally`, even if closing the subscription fails. If
construction raises, ownership remains with the caller.

Iteration accepts only these closed envelopes:

- `{"event":"invalidate","data":{"version":1,"resources":["tasks"]}}`
- `{"event":"resync","data":{"version":1}}`
- `{"event":"auth-required","data":{"version":1}}`
- `None` writes a heartbeat comment, never an event acknowledgement.

Resources are 1–32 distinct logical ASCII names matching
`[a-z][a-z0-9_-]{0,63}`. Data JSON is bounded to 4096 UTF-8 bytes. Extra keys,
URLs, HXML, identifiers, cursors, and unknown event names are not accepted.
The application must constrain resource names further and authorize before
headers and each event/heartbeat; this adapter does not authenticate users or
make delivery durable. It implements no event ID, replay or automatic retry.

Normal exhaustion, iterator errors, cancellation and Django's public `close()`
path close the iterator and invoke the explicit owner callback exactly once.
The public ASGI wrapper closes remaining owners in `finally`, including a
send failure before iteration where Django may not call response close. A
ContextVar carries the mutable request scope through Django's sync adapters.
Normal cleanup unregisters the owner; closed scopes reject new owners, and
non-HTTP scopes pass through. Same-loop `close()` schedules cleanup;
it cannot synchronously wait on its own event loop. ASGI's other-thread close
waits for the bounded cleanup task.

Cleanup permits two seconds and requires cancellation-cooperative callbacks.
Errors/timeouts log only fixed codes, never exception contents. A callback that
ignores cancellation, or a loop shut down before cleanup, cannot be guaranteed
closed by Python; a timeout is **not** a successful cleanup acknowledgement.
Run `pytest -q tests/test_realtime_response.py` for actual Django ASGI pre-frame
error/disconnect/cancellation and never-iterated ownership regressions.
