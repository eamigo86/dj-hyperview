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
