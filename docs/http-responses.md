# Return Hyperview documents and fragments

Use a full-document response for navigation and a fragment response for an
in-place update. dj-hyperview applies the correct media type and validates the
response shape before bytes reach the client.

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


response = HyperviewFragmentResponse("<view id='saved'><text>Saved</text></view>")
```

A fragment must not be wrapped by `doc`, `navigator`, `screen`, or `body`.
Those roots are valid only in their full-document context and raise
`TemplateValidationError` when returned as a fragment.

## Response contract

- Hyperview responses are always UTF-8. An explicit non-UTF-8 charset raises
  `ValueError`.
- Lazy template responses validate after Django finishes rendering.
- Fragment responses enforce well-formed XML, configured resource limits, and
  the client-safe root restriction.
- A navigation endpoint and an update endpoint should not share one response
  class merely because they render related markup.

If Hyperview reports `XMLRestrictedElementFound`, inspect the initiating action,
the response media type, and the first response element together. For an
in-place action, return only the element that replaces or extends the target.

