# Installation

[Step-by-step SSE guide](realtime.md): setup, negotiated changes, compatibility examples and host responsibilities.

Install dj-hyperview into a supported Django project:

```bash
uv add dj-hyperview
```

dj-hyperview is tested on Python 3.12, 3.13, and 3.14 with Django 5.2 and 6.1.
Version 0.1.0b3 requires Python 3.12 or newer without an upper bound. Newer
interpreters are not blocked by package metadata but are not in the CI matrix.

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
The registry speeds up repeated checks with internals of `xmlschema` that are not
documented API. The fast registry is enabled only when the installed
`xmlschema.__version__` is exactly `4.3.2`, the release verified by the differential
tests. Every other supported release (`>=4.3,<5`), including later 4.3 patches,
uses plain `xmlschema.XMLSchema11`: validation still runs in full, but takes longer.
Adding another release to the allowlist requires explicit validation of that exact
release first.
The faster registry remembers an accepted attribute value only for a type whose
check depends on the value alone; a type with an XSD 1.1 assertion facet is always checked in
full. Differential tests and reasoning, not a proof for every schema, support that
it gives the verdicts of the plain class.
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
client or connect. The supplied lock resolves 8.1.0, the only version the live
Redis job runs against; 7.4.1 is the lower bound that the package declares and
does not test. No Redis server is installed by this extra. Provide a separately operated Redis service only when enabling
application-owned realtime. See [SSE ownership](http-responses.md#owned-asynchronous-sse-responses)
and [broker configuration](configuration.md#optional-realtime-broker).
