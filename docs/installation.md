# Installation

[Step-by-step SSE guide](realtime.md): setup, negotiated changes, compatibility examples and host responsibilities.

Install dj-hyperview into a supported Django project:

```bash
uv add dj-hyperview
```

The supported baseline is Python 3.12 or newer, below Python 3.15, with
Django 5.2 or 6.1.

Register the package app:

```python
INSTALLED_APPS = [
    # Your project apps.
    "dj_hyperview",
]
```

The base app performs no database, cache, or network access during startup.
Database-backed templates and admin integration remain optional.

## Automatic schema validation

`xmlschema` is a normal dependency of the base package. Every rendered Hyperview
document and fragment uses the same corrected XSD 1.1 registry automatically.
No schema profile, validation mode, or callback needs to be configured.
The deprecated `[schema]` installation extra is an empty compatibility alias:
it does not enable a second validation mode or install an additional package.

## Optional Admin editor

```bash
uv add "dj-hyperview[editor]"
```

The extra adds Ace, not a different schema. The base package does not import
`django_ace`. Enable the editor by adding `"django_ace"` to `INSTALLED_APPS` and
setting `HYPERVIEW["ADMIN"]["EDITOR"]` to `True`. Installing the extra alone does
not change existing admin forms.

An upgrade changes validation for all rendered HXML. Review effective database
overrides as well as filesystem templates before adoption; follow the
[coordinated upgrade procedure](release-rollback.md#automatic-validation-adoption).

Continue with the [Quick Start](quickstart.md) to serve a filesystem-backed
screen, or open the complete [configuration reference](configuration.md).

## Optional realtime hints

Configure it under `HYPERVIEW["REALTIME"]` with `REDIS_URL` and `NAMESPACE`;
omit the section or use `None` to leave it disabled. Preserve the other HYPERVIEW
sections when adding it. The [complete settings example](configuration.md) shows
all sections together. Reading settings or running checks does not connect Redis.

`uv add "dj-hyperview[realtime]"` adds redis-py `>=7.4.1,<9`. Redis remains
optional: package startup, template signals and SSE framing do not import the
client or connect. The supplied lock continues to resolve the existing 8.1.0;
transport capability tests also exercise 7.4.1. No Redis server is installed by
this extra. Provide a separately operated Redis service only when enabling
application-owned realtime. See [SSE ownership](http-responses.md#owned-asynchronous-sse-responses)
and [broker configuration](configuration.md#optional-realtime-broker).
