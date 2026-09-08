# Changelog

This page records user-visible changes for each published version.

## Unreleased

No changes yet.

## 0.1.0a12 — 2026-09-08

### Added

- Opt-in Admin template preview with configured example-data scenarios, unsaved
  draft rendering, structured diagnostics, and an isolated static web view.
  Preview is disabled by default, keeps the editor buffer unchanged, and never
  publishes a template. See [Admin template preview](admin-preview.md).

### Fixed

- Static preview now applies styles declared at document level and refreshes
  visual warning state when selecting another screen.

## 0.1.0a11 — 2026-09-08

### Upgrade notes

- Django `FileBasedCache` and its subclasses are rejected because their
  non-atomic `add()` cannot protect invalidation barriers. Configure a compatible
  backend or disable Hyperview caching; `bypass` does not suppress this error.
  See [cache migration guidance](cache-consistency.md).
- `bulk_create(update_conflicts=True)` now raises `NotSupportedError` before
  consuming input or writing. Normal inserts and `ignore_conflicts` remain
  supported; use [publication services](database-admin.md) for updates.
- Run the read-only `check_hyperview_templates --database ALIAS` command only on
  authorized databases before deployment. Invalid names, incorrect identities,
  or duplicates require a backed-up, human-reviewed repair, not an automatic
  migration. See [historical integrity checks](database-admin.md#check-historical-template-integrity).

### Added

- Opt-in `SCHEMA_PROFILE="compatible-0.110.0"` permits percentages in the nine
  style margin attributes without broadening unrelated types. The default
  `upstream-0.110.0` and standalone upstream helpers retain their existing
  meaning. Validation and autocomplete use the selected profile together. See
  [custom schemas](custom-schemas.md).
- The package README and documentation landing page now link to
  [HyperTodo](https://github.com/eamigo86/HyperTodo), the maintained Django and
  Expo test application.

### Fixed

- Declaration scanning is linear, including unclosed Django delimiters; only
  raw template sources interpret Django comments. Rendered XML retains its
  independent safety checks and escaping. See [XML security](security.md).
- An initial BOM no longer conceals incompatible XML encoding declarations;
  valid UTF-8 BOM content remains unchanged.
- Filesystem permission and I/O failures remain `SourceUnavailable` on Python
  3.14 instead of selecting a lower-priority template. See [filesystem sources](filesystem.md).
- Read-only Admin users can view templates while the Ace editor is enabled.
- HXML formatting preserves meaningful whitespace, mixed content, CDATA,
  preformatted text and Django syntax, including `blocktranslate` and
  `blocktrans` bodies; ambiguous input remains unchanged. See [Admin
  authoring](database-admin.md).
- Nested deletions use both database alias and primary key when tracking
  invalidations; commit and rollback keep their existing guarantees.
- Generator-based `update_fields` is consumed once without dropping fields.
- Extra XSDs reject `xs:override`; only the fixed bundled compatibility
  adaptation uses it. Validation and completion both inspect transitive local
  references before reusing dependency-aware caches. See [schema safety](security.md).
- The compatibility runner enforces **lines ≥95% and branches ≥95%** separately
  after combining base and Admin coverage, while retaining the combined gate.
  Missing, malformed and stale reports cannot pass. See [testing](testing.md).

## 0.1.0a10 — 2026-09-07

### Added

- `ADMIN.PERMISSION` as an authoritative callable or dotted-path policy for
  stored-template creation, changes, and deletion, with a superuser-only default.
- Detailed custom-schema guidance for project-owned namespaced HXML elements,
  validation, and editor autocomplete.

### Changed

- The HXML Ace editor now fills the available Django 5.2 and Django 6.1 admin
  form width instead of collapsing to its gutter inside the admin flex layout.

### Security

- Template mutation callbacks grant access only when they return the literal
  boolean `True`; import problems fail system checks and runtime failures deny
  access.

## 0.1.0a9 — 2026-09-07

### Added

- A versioned Hyperview 0.110.0 XSD registry, provenance, checksums, and a
  deterministic completion catalog.
- Optional `[schema]` support for XSD 1.1 validation of rendered documents and
  fragments, including project-owned custom schemas.
- An optional `[editor]` profile with a CSP-safe HXML Ace editor, contextual
  completion, Django-template highlighting, safe formatting, and theme-aware
  presentation.
- Safe line and column coordinates on `TemplateValidationError` when available.

### Changed

- Database publication now compiles Django template syntax before any write or
  revision increment, without rendering context-dependent output.
- Schema and catalog caches now follow settings and local-file fingerprints.
- CI now tests editor JavaScript on Node 24 and smoke-tests base, schema, and
  editor wheel profiles.

### Security

- Custom schemas reject remote references, local-root escapes, and incompatible
  duplicate declarations.
- The admin catalog endpoint requires authentication and model view permission;
  editor assets are local and avoid inline scripts.

## 0.1.0a8 — 2026-09-06

### Added

- First-class eager and lazy fragment responses with the
  `application/vnd.hyperview_fragment+xml` media type.
- A PEP 561 `py.typed` marker and the public `dj_hyperview.__version__` value.
- A standalone Expo and Hyperview mobile Getting Started guide.

### Changed

- Hardened database publication, byte-exact identity handling, legacy-row
  recovery, mutation routing, and transaction-aware invalidation.
- Isolated the package template engine from unsafe consumer engine options.
- Preserved cache-safe source results across mixed source chains.
- Reported absent source configuration and missing filesystem roots as
  actionable Django system-check warnings.
- Reused validated settings and the default rendering engine until a dependent
  Django setting changes.
- Rejected unsafe `bulk_create()` names and invalidated cache state after bulk
  writes, including conflict updates.

### Compatibility

- Tested against Django 5.2 and Django 6.1 on supported Python versions.
- Fragment update endpoints must return a bare client-safe element. Full HXML
  documents continue to use `application/vnd.hyperview+xml`.
