# dj-hyperview

Serve consumer-owned Hyperview markup through Django without bundling
application screens into the package.

## Start here

1. [Install dj-hyperview](installation.md).
2. [Configure template resolution](configuration.md).
3. Resolve a canonical template name through the public package API.

The package owns resolution, validation, raw-template caching, HTTP responses,
and optional database publication. Your Django project owns every screen,
schema, route, and application decision.

## Supported baseline

- Python 3.12 through 3.14.
- Django 5.2 and 6.1.
- Hyperview markup contract 0.110.0.

dj-hyperview does not ship application screens, runtime XML or HXML files,
Redis, or a required database app.

Choose the next guide for your source:

- [Use filesystem sources](filesystem.md) for the smallest deployment.
- [Publish through the database](database-admin.md) when editors need admin.

The [development documentation](development/README.md) records architecture,
decisions, task history, and release gates.
