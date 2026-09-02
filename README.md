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
