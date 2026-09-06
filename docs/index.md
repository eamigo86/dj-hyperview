# dj-hyperview

Serve consumer-owned Hyperview markup through Django without bundling
application screens into the package.

## Start here

1. [Install dj-hyperview](installation.md).
2. [Serve your first screen](quickstart.md).
3. [Configure template resolution](configuration.md) for your deployment.
4. Resolve a canonical template name through the public package API.

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
- [Configure cache consistency](cache-consistency.md) only when caching is needed.
- [Harden names and XML](security.md) before accepting authored templates.
- [Return documents and fragments](http-responses.md) with the right client contract.
- [Create a mobile client](mobile-getting-started.md) without cloning an example app.
- [Test a consumer integration](testing.md) across supported Django versions.
- [Release and roll back](release-rollback.md) with reproducible gates.
- [Review release changes](changelog.md) before upgrading.

Use the [public Python API reference](api-reference.md) when integrating package
objects directly. Contributors should begin with the
[contributing guide](contributing.md).
