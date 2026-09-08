# Database source and admin

Publish validated templates through Django's ORM and optionally edit them with
the standard admin. Both capabilities remain absent until the contrib app is
installed.

Stored source is executable Django template code. Anyone allowed to add or
change a `HyperviewTemplate` must have developer-level trust; XML validation
does not sandbox template tags, filters, or render-context access.

Template mutations are therefore restricted to superusers by default:

```python
HYPERVIEW = {
    "ADMIN": {
        "PERMISSION": lambda request: request.user.is_superuser,
    },
}
```

The explicit lambda above matches the built-in default. Prefer a dotted callback
in production so the policy can be imported and tested directly:

```python
# sample_app/permissions.py
from django.http import HttpRequest


def can_edit_hyperview(request: HttpRequest) -> bool:
    """Authorize trusted mobile-interface maintainers."""
    return request.user.has_perm("dj_hyperview_database.change_hyperviewtemplate")
```

```python
HYPERVIEW = {
    "ADMIN": {
        "PERMISSION": "sample_app.permissions.can_edit_hyperview",
    },
}
```

`ADMIN.PERMISSION` controls add, change, and delete as one authoritative policy.
It receives the current `HttpRequest` and grants access only by returning the
literal boolean `True`. Exceptions and non-boolean results fail closed. Admin
login still requires an active staff user, while ordinary Django model view
permission can provide read-only access without granting template mutation.
Read-only template pages display escaped content without edit, delete, or format
controls, including when the optional Ace editor is enabled.

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
    "ADMIN": {
        "EDITOR": True,
        "PERMISSION": "sample_app.permissions.can_edit_hyperview",
    },
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

**Format and Validate** first indents only structural whitespace in known
element-only Hyperview containers. It preserves original opening tags, quoted
attribute values, significant text and mixed content, CDATA, custom component
subtrees, and preformatted or `xml:space`-preserving content. Django tokens and
complete `comment`/`verbatim` bodies remain byte-for-byte unchanged; no
replacement markers or XML reserialization are used. Would-be Django tokens
whose delimiters span an LF newline are rejected unchanged; raw
`comment`/`verbatim` bodies may still span multiple lines.

Balanced conditional branches containing complete elements can be formatted.
Ambiguous or incomplete markup, branch-dependent opening/closing tags, and
unsupported template constructs are left untouched and reported beside the
editor. Repeated successful formatting is idempotent. This conservative
formatter is not a substitute for publish-time template validation. Ace
synchronizes back to Django's textarea before submission, so standard form
processing remains the source of truth.

After formatting, the same action checks the current unsaved name and Ace
buffer without publishing or mutating the stored template. If conservative
formatting cannot safely rewrite the source, validation still runs against the
unchanged source and the Admin reports both results. Validation verifies the
canonical name,
nonempty source, UTF-8 and size limits, forbidden declarations, and Django
template syntax. It also performs static XSD catalog checks for element names,
attribute names, required attributes, and literal enumerated values. The catalog
uses the selected `SCHEMA_PROFILE` and merges configured `EXTRA_SCHEMAS`, so the
same project components offered by autocomplete participate in this check. Safe
diagnostics include the source line when it is available. The request uses the
existing Admin CSRF and `ADMIN.PERMISSION` boundaries; it does not save content,
increment `revision`, or invalidate caches.

The standard **Save**, **Save and add another**, and **Save and continue
editing** actions run this same format-and-validate preflight automatically.
Validation errors keep the form open and display the diagnostics; warning-only
results allow the selected Django Admin action to continue. The exact submit
action is preserved. If formatting is unsafe, the unchanged source is validated
and can still be saved when valid.

The browser preflight improves feedback but is not the trust boundary. With the
editor enabled, the Django `ModelForm` repeats the source and static-schema
checks before every write, so bypassing or disabling JavaScript cannot persist a
draft that those checks reject.

This action deliberately does not render the template and therefore does not
run full rendered XSD validation. Dynamic attribute values are deferred, and the
check cannot prove the final XML structure, resolve includes, exercise one final
conditional branch, or evaluate type restrictions and XSD assertions that depend
on rendered values. If dynamic markup prevents safe static analysis, the Admin
reports a warning instead of claiming a complete result. Authoritative checks
still occur through rendered-response validation. A successful draft check means
that the source compiles and its statically visible schema declarations pass; it
does not mean that every possible rendered document is valid.

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
import primitive, not a substitute for the publication services. Normal inserts
and `ignore_conflicts=True` remain supported; ignored conflicts never update
existing content. `update_conflicts=True` raises `NotSupportedError` before
consuming the supplied iterable, executing SQL, or scheduling invalidation.
Replace conflict-updating imports with `publish_template()` for content changes
and `rename_template()` for name changes, passing the expected revision.
Raw SQL provides no automatic publication contract.

`save(update_fields=...)` accepts single-use iterables, including generators,
and includes the matching identity when saving a new name. Batch deletion
tracks the database alias together with each primary key: nested deletions
with matching primary keys in another database retain their own commit-aware
invalidation. A rollback on either alias discards only that alias's callbacks.

A row imported by raw SQL or an older release can contain a name that is no
longer canonical. Such a row is never resolved, but it remains recoverable:
individual admin deletion works, and a literal canonical `QuerySet.update()` can
repair its name. New invalid names remain rejected.


## Check historical template integrity

Before deploying an upgrade from a release that allowed conflict-updating bulk
inserts, inspect each explicitly authorized template database:

```console
python manage.py check_hyperview_templates --database default
```

The database alias is required. The command reads only primary keys, names, and
identities in batches; it never loads template content, writes data, increments
revisions, or invalidates caches. Diagnostics are ordered by primary key and
contain identifiers and issue codes, not template names or source content:

```text
pk=20: identity_mismatch, duplicate_identity (first_pk=10)
```

It reports unsafe/noncanonical or overlong names, identities that do not match
the stored name's SHA-256, and duplicate expected identities even when the
stored identity column contains different values. A nonzero exit status means
integrity issues were found or the check could not complete. A successful check
does not validate template content or its revision history.

If issues are reported, stop deployment and take a backup before any repair.
Have a maintainer determine the intended names and content, especially for
duplicates; the command never picks a winning row or repairs data automatically.
Once an authorized repair is complete, run the check again and rotate only
`HYPERVIEW["CACHE"]["NAMESPACE"]` consistently across Hyperview workers. Never
flush an entire shared Django cache to recover template state.
