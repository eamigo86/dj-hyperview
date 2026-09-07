# Database source and admin

Publish validated templates through Django's ORM and optionally edit them with
the standard admin. Both capabilities remain absent until the contrib app is
installed.

Stored source is executable Django template code. Anyone allowed to add or
change a `HyperviewTemplate` must have developer-level trust; XML validation
does not sandbox template tags, filters, or render-context access.

## Enable the database source

Add the optional app and source to project settings:

```python
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "dj_hyperview",
    "dj_hyperview.contrib.database",
]

HYPERVIEW = {
    "SOURCES": [
        {
            "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
            "OPTIONS": {"using": "default"},
        },
    ],
}
```

Apply the package migration on the same alias:

```console
python manage.py migrate dj_hyperview_database --database default
```

An active exact-name row resolves with its stored revision and origin. Inactive
or missing rows fall through to the next configured source. Put the database
source before a filesystem source when editor content should win.

An explicit `using` alias is stable and cacheable. Omitting it delegates to the
Django write/read router and intentionally disables source caching because the
selected database can vary per request.

Stored names use a separate SHA-256 identity, so lookup and uniqueness remain
byte-exact even when the database column's default collation is
case-insensitive. `Home.xml` and `home.xml` are therefore distinct templates.

## Publish through the service boundary

The public services validate names and content, lock existing rows, increment
revisions once, and use the selected write database:

Publish-time content validation enforces encoding, size, and forbidden
declaration safety. It deliberately accepts complete documents as well as
multi-root and text-only partials containing Django syntax. Structural XML and
schema guarantees apply to the final composed output during render-time validation
when the configured mode is render or publish_and_render.

```python
from dj_hyperview.contrib.database.services import (
    delete_template,
    publish_template,
    rename_template,
)

created = publish_template("screens/home.xml", "<view />", using="default")
updated = publish_template(
    created.name,
    "<view><text>Updated</text></view>",
    expected_revision=created.revision,
    using="default",
)
renamed = rename_template(
    updated.name,
    "screens/start.xml",
    expected_revision=updated.revision,
    using="default",
)
delete_template(
    renamed.name,
    expected_revision=renamed.revision,
    using="default",
)
```

`PublicationConflict` reports missing, stale, or conflicting state without
database details. A returned result is persisted in the current transaction; an
outer rollback can still discard it.

Model signals schedule invalidation with `transaction.on_commit` on the mutation
alias. Therefore a rollback has no cache effect. A cache failure after a
successful commit remains observable and does not mean the database rolled back.

## Add the standard admin

Add `django.contrib.admin` and its normal dependencies, include your project's
admin URLs, and run Django checks:

```python
from django.contrib import admin
from django.urls import path

urlpatterns = [path("admin/", admin.site.urls)]
```

The contrib app registers
`HyperviewTemplateAdmin` automatically during admin discovery. Create, edit,
rename, and individual delete use the publication services; change and delete
forms carry a protected revision token to reject stale submissions.

`revision` is an optimistic-concurrency and cache identity token. It is not
history or rollback: publishing replaces the stored content, and the package
does not retain earlier revisions.

## Enable the HXML editor

The standard textarea is the default and needs no extra dependencies. For
schema-aware completion, local formatting, and Django-template highlighting,
install the optional editor profile:

```bash
uv add "dj-hyperview[editor]"
```

```python
INSTALLED_APPS = [
    "django.contrib.admin",
    "django_ace",
    "dj_hyperview",
    "dj_hyperview.contrib.database",
]

HYPERVIEW = {
    "ADMIN": {"EDITOR": True},
    "EXTRA_SCHEMAS": [BASE_DIR / "schema" / "hypertodo.xsd"],
    "VALIDATION": {
        "SCHEMA": "dj_hyperview.validate_hyperview_schema",
    },
}
```

The editor uses package-owned assets, django-ace strict CSP mode, automatic
light and dark themes, line numbers, search, wrapping, full screen, and
contextual completion for elements, unused attributes, enumerated values, and
configured namespace prefixes. Its catalog endpoint requires authentication
and view permission for the template model.

**Format HXML** temporarily protects Django variables, tags, and comments,
formats the XML, and restores the exact template tokens. Unsafe or incomplete
input is left untouched and reported beside the editor. Ace synchronizes back
to Django's textarea before submission, so standard form processing remains the
source of truth.

Publication compiles Django template syntax before writing or incrementing a
revision. Invalid tags, variables, or blocks are reported next to `content`.
This check does not render the template: context-dependent XML and schema
validation still occur when the response is rendered. XSD errors expose safe
line and column coordinates when the underlying parser provides them.

Direct `save()` does not call `full_clean()`. Prefer the services or the admin
for validated publication. The package QuerySet batches `update()` and `delete()`
while scheduling one commit-aware invalidation for all canonical affected names.
`bulk_create()` rejects unsafe names and schedules invalidation, but still
bypasses content compilation, validation, and revision semantics. It is an
import primitive, not a substitute for the publication services. Raw SQL
provides no automatic publication contract.

A row imported by raw SQL or an older release can contain a name that is no
longer canonical. Such a row is never resolved, but it remains recoverable:
individual admin deletion works, and a literal canonical `QuerySet.update()` can
repair its name. New invalid names remain rejected.
