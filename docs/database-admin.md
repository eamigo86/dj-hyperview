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

The callback runs at most once per request: the first add, change or delete
check stores its result on the request, and later checks of that request reuse
it. A new request always calls it again, so a callback that depends on mutable
state is still evaluated for every request. The changelist does not load the
template bodies, and the admin form stores `content` exactly as submitted,
including leading and trailing whitespace.

To mount the admin on a custom `AdminSite`, register `HyperviewTemplateAdmin`
there. The editor's validation and completion endpoints are resolved on the site
that serves the form, not on the default `admin` site.

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
schema guarantees apply to the final composed output during render-time validation,
which is always active and cannot be disabled.

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

`publish_template()` keeps the `active` value of an existing template unless you
pass `active`, and publishes a new template as active. Pass `active=True` to
activate an inactive template while updating it.

`PublicationConflict` reports missing, stale, or conflicting state without
database details. A returned result is persisted in the current transaction; an
outer rollback can still discard it.

Model signals schedule invalidation with `transaction.on_commit` on the mutation
alias, for `HyperviewTemplate` and for proxy models of it. Therefore a rollback
has no cache effect. A cache failure after a successful commit remains observable
and does not mean the database rolled back. The same callback also sends the public
[committed invalidation hint](api-reference.md#committed-template-invalidations),
including when caching is disabled; receiver errors do not undo the write. The
receivers are connected to those model classes only, so every other model in your
project keeps Django's fast path for bulk deletes.

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
}
```

The editor always uses the dark Monokai theme, regardless of the Django Admin theme or system color preference.

The editor uses package-owned assets, django-ace strict CSP mode, line numbers, search, wrapping, full screen, and contextual completion for elements, unused attributes, enumerated values, and configured namespace prefixes. Its catalog endpoint requires authentication and view permission for the template model, and answers 404 unless `ADMIN.EDITOR` is enabled, like the validation endpoint. The source uses a 15-pixel editor font and inserts two-space soft tabs; pressing Tab never writes a literal tab.

**Format and Validate** first indents only structural whitespace in known
element-only Hyperview containers. It preserves original opening tags, quoted
attribute values, significant text and mixed content, CDATA, custom component
subtrees, and preformatted or `xml:space`-preserving content. Django tokens and
complete `comment`/`verbatim` bodies remain byte-for-byte unchanged; no
replacement markers or XML reserialization are used. `{% load %}` renders
nothing, so it does not stop formatting; tags that render output, such as
`{% include %}` and `{% hv_csrf_token %}`, keep their enclosing element as written.
Would-be Django tokens whose delimiters span an LF newline are rejected unchanged;
raw `comment`/`verbatim` bodies may still span multiple lines.

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
template syntax. Django's parser checks block closure and each installed tag's
grammar and arguments, including tags contributed by configured template
libraries; dj-hyperview does not maintain a second hard-coded tag list. The
parser's actionable error is shown beside the source. It also performs
static XSD catalog checks for element names,
attribute names, required attributes, literal typed values, and statically
visible child placement and order. A Django expression used as element text or
as a quoted attribute value is treated as an unknown scalar: its concrete value
is deferred, while the owning element, attribute, position, and surrounding
structure are still checked. The catalog uses the automatic corrected schema
and merges configured `EXTRA_SCHEMAS`, so the same project components offered
by autocomplete participate in this check. Safe diagnostics include the source
line when it is available. The request uses the existing Admin CSRF and
`ADMIN.PERMISSION` boundaries; it does not save content, increment `revision`,
or invalidate caches. Literal `{% include "..." %}` targets are resolved through
the configured Hyperview source precedence, recursively compiled, and checked
both independently and in their parent position. Missing targets and include
cycles are errors. Expansion stops after 64 include edges, or when the draft and
the includes read so far would exceed `VALIDATION.MAX_BYTES` together, and the
includes that were not expanded produce the informational note described below
rather than an error, because the result is incomplete, not wrong.

The check reads Django syntax the way Django does, from the tokens Django's lexer
produces. A `{% comment %}` block hides everything up to `{% endcomment %}` from
the XML checks, whatever note follows the tag name, as in
`{% comment "temporarily disabled" %}`, so commented-out markup is never
validated. A Django delimiter never spans a line break: a `{# ... #}`,
`{{ ... }}` or `{% ... %}` split over two lines is literal text, exactly as it is
when the template is served, and the check reports what that text does to the
document, such as text inside an element that allows only elements. A variable
that applies the `safe` filter in element text, such as `{{ fragment|safe }}`, may
render markup, so its position is runtime-dependent and gets the informational
note instead of an error. A `{% verbatim %}` body is not analyzed and gets the
same note, including when markup it may supply is missing from the rest of the
document, such as a required child or half of a tag.

The check is bounded like rendering. Before any schema work it applies
`VALIDATION.MAX_NODES` and `VALIDATION.MAX_DEPTH`, and the fixed limits of 128
attributes on an element, 8 in-scope namespaces, and 32 namespace declarations on
one path, to the template source, and it reports a source over a limit as an error
with the same stable code: `max_nodes`, `max_depth`, or `max_attributes`. Because
the source still holds every branch of a conditional, the counts can exceed those
of any single rendered document. Source that nests blocks, such as `{% if %}`, more
than 100 levels deep is reported as `too_deep` instead of failing the request:
Django's recursive parser compiles about 490 levels, but rendering fails at about
330 in an empty stack, so deeper source could be saved and then fail when served. The
response lists at most 50 diagnostics, errors before notes, and ends with an
informational `diagnostics_truncated` entry when more were found. Checking time
grows in proportion to the size of the draft and of the includes it expands, which
`VALIDATION.MAX_BYTES` bounds together.

An informational note — **Static checks passed. Final HXML will be validated
when served.** — is normal for dynamic templates, not a warning or a validation
failure. It means the static checks passed, but Django markup such as a
conditional branch, template inheritance, or an include whose target is a variable
can add or remove
nodes beginning at the reported line. Literal includes are the structural tag
that this static pass composes; they are not deferred merely because they are
includes. The package validates final rendered HXML when the response is served.
The note keeps its source line and **Go to source** button but uses neutral
styling rather than warning colors. Its diagnostic code remains
`schema_static_incomplete`, with severity `info`. Actual source or schema errors
remain errors and block the Admin save actions.

The standard **Save**, **Save and add another**, and **Save and continue
editing** actions run this same format-and-validate preflight automatically.
Validation errors keep the form open and display the diagnostics; informational
notes and warning-only results allow the selected Django Admin action to
continue. The exact submit action is preserved. If formatting is unsafe, the unchanged source is validated
and can still be saved when valid.

The browser preflight improves feedback but is not the trust boundary. With the
editor enabled, the Django `ModelForm` repeats the source and static-schema
checks before every write, so bypassing or disabling JavaScript cannot persist a
draft that those checks reject.

This action deliberately does not render the template and therefore does not
run full rendered XSD validation. Dynamic attribute values are deferred, and the
check cannot resolve variable-selected includes, choose one final conditional
branch, or evaluate type restrictions and XSD assertions that depend on rendered
values. If dynamic markup prevents safe static analysis, the Admin reports an
informational note instead of claiming a complete result. Authoritative checks
still occur through rendered-response validation. A successful draft check means that the
source compiles and its statically visible schema declarations pass; it does not
mean that every possible rendered document is valid.

### Register application schema extensions

Validation is always active. Register only the client functionality your
application adds; no profile or schema callback is needed:

```python
HYPERVIEW = {
    "ADMIN": {"EDITOR": True},
    "SCHEMA_EXTENSIONS": {
        "BEHAVIORS": {
            "show-toast": {
                "ATTRIBUTES": {
                    "message": {"TYPE": "string", "REQUIRED": True},
                    "duration": {"TYPE": "string", "ENUM": ["short", "long"]},
                },
            },
        },
        "ELEMENT_ATTRIBUTES": {
            "image": {"variant": {"TYPE": "string", "ENUM": ["face", "fingerprint"]}},
        },
    },
}
```

Registrations describe client functionality your application already implements;
they do not install behavior handlers. Attributes belong only to the registered
literal action or named built-in element. An unknown or Django-generated action
never exposes the union of custom attributes in autocomplete. Qualified
attributes retain their declared, in-scope prefixes: `alert:message` is distinct
from the unqualified custom `message`, including when prefixes are shadowed.

The source checker and rendered validator share the compiled registry and
conditional behavior types. In addition to required attributes and enums, the checker
checks literal primitive values and type restrictions, including width syntax.
Dynamic action names, variable-selected includes, and structural branches produce
an incomplete-analysis informational note; dynamic scalar values do not. Valid
static declarations can still be checked without rendering.
Informational notes do not block the existing save workflow. Exact rendered XSD
validation remains authoritative for composed structure, assertions, and runtime values.
Neither autocomplete nor source validation rewrites template XML.

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
