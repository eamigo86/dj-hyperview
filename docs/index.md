# dj-hyperview

Serve consumer-owned Hyperview markup through Django without bundling
application screens into the package.

## Start here

1. [Install dj-hyperview](installation.md).
2. [Serve your first screen](quickstart.md).
3. [Configure template resolution](configuration.md) for your deployment.
4. Resolve a canonical template name through the public package API.
5. Explore [HyperTodo](https://github.com/eamigo86/HyperTodo), the maintained
   Django and Expo test application for dj-hyperview.

The package owns resolution, validation, raw-template caching, HTTP responses,
and optional database publication. Your Django project owns every screen,
schema, route, and application decision.

## Supported baseline

- Python 3.12 through 3.14.
- Django 5.2 and 6.1.
- Hyperview markup contract 0.110.0.

dj-hyperview does not ship application screens, runtime application HXML,
Redis, or a required database app.

Choose the next guide for your source:

- [Use filesystem sources](filesystem.md) for the smallest deployment.
- [Publish through the database](database-admin.md) when editors need admin or
  optional schema-aware HXML authoring.
- [Preview unsaved Admin templates](admin-preview.md) with explicit example data
  before publishing them.
- [Configure cache consistency](cache-consistency.md) only when caching is needed.
- [Harden names and XML](security.md) before accepting authored templates.
- [Add custom HXML elements](custom-schemas.md) when a mobile client extends
  the Hyperview vocabulary.
- [Return documents and fragments](http-responses.md) with the right client contract.
- [Create a mobile client](mobile-getting-started.md), or compare the result with
  the HyperTodo test application.
- [Test a consumer integration](testing.md) across supported Django versions.
- [Release and roll back](release-rollback.md) with reproducible gates.
- [Review release changes](changelog.md) before upgrading.

Use the [public Python API reference](api-reference.md) when integrating package
objects directly. Contributors should begin with the
[contributing guide](contributing.md).
