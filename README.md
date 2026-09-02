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
