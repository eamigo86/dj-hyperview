# Changelog

This page records user-visible release changes, including unpublished attempts.

## 0.1.0a21 — 2026-09-09

### Added

- Centralized optional `HYPERVIEW.REALTIME` configuration with exact `REDIS_URL`
  and `NAMESPACE`, frozen `get_settings().realtime`, sanitized `E022` checks and
  no Redis import/network during settings access. Omitted/None disables it;
  cache-alias adaptation is not implemented. The configuration guide now opens
  with a complete executable example, then explains each section in plain English.
- Step-by-step SSE guide with verified ASGI/publication snippets, typed
  list/form examples and an explicit package-versus-application integration boundary.
- Optional `[realtime]` Redis broker with immutable after-commit capture, actual
  subscription ACK admission, bounded queues and conservative resync hints.
- Public `realtime_asgi` request ownership and `sse_response` closed framing;
  cleanup survives sync middleware adaptation and pre-frame disconnects.
  These are transport primitives, not application authorization or replay.

- Public frozen `TemplateInvalidation` events and robust `template_invalidated`
  signal for supported database template mutations after commit. Notifications
  share the cache callback, including disabled/failing cache paths, without
  suppressing existing cache exceptions or requiring Redis/network transport.

### Fixed

- Fresh-process realtime tests now explicitly locate the repository source,
  independent of an inherited `PYTHONPATH`. Redis-import and network prohibitions
  remain enforced; no runtime transport behavior changed.

## 0.1.0a20 — 2026-09-09 (not published)

- Release checks failed before artifact staging and publication because three
  fresh-process tests could not import the source package in CI. The tag is
  retained unchanged; the corrected SSE release is a21 above.

## 0.1.0a19 — 2026-09-09

### Fixed

- Context-free Admin validation now ignores XML comments, CDATA sections, and
  processing instructions while locating Django tokens. Literal
  `{% include %}` tags that follow documentation containing quotes are expanded
  correctly instead of producing false text-content schema errors.

## 0.1.0a18 — 2026-09-09

### Added

- Context-free Admin validation uses Django's parser for actionable syntax
  diagnostics and recursively resolves every literal `{% include %}` through
  the configured Hyperview source precedence. Included source is checked both
  independently and in its parent position; missing, unsafe, unavailable,
  cyclic, and excessive expansion is rejected without exposing private source
  details.
- Statically visible HXML child placement and order are validated against the
  same corrected schema used for rendered responses. Django expressions in
  text or quoted attributes are treated as an unknown scalar, preserving
  structural validation without inventing runtime values.

### Changed

- The Admin HXML editor uses a 15-pixel font and two-space soft tabs.
- Runtime schema failures now explain unexpected children, incomplete content,
  forbidden or missing attributes, and invalid typed values without including
  rendered data or internal paths.

### Fixed

- Mutually exclusive Django branches no longer create false child-order errors.
  Conditional structure, inheritance, and variable-selected includes instead
  produce an explicit incomplete-analysis warning and remain subject to final
  rendered-response validation.

## 0.1.0a17 — 2026-09-08

### Fixed

- Context-free Admin validation no longer treats non-rendering `{% load %}` tags
  or inline Django comments as incomplete dynamic HXML. When later runtime
  values remain, the warning points to the first value that actually requires
  rendering instead of blaming the template preamble.

## 0.1.0a16 — 2026-09-08

### Added

- One automatically enforced Hyperview 0.110.0 schema includes narrowly verified
  text, image, accessibility, date-field, and percentage-margin corrections.
  Only `style@width` is constrained to its supported numeric forms. Schema
  revision tracking is internal; upstream resources remain unchanged.
- Typed `SCHEMA_EXTENSIONS` declarations describe application-owned behavior
  attributes and extra attributes on specific built-in elements without opening
  standard actions or allowing arbitrary attributes. Rendered validation,
  context-free Admin checks, and editor completion share this single contract.
  See [custom schema declarations](custom-schemas.md).
- `HYPERVIEW_VALIDATION_CONTRACT="automatic-xsd-v1"` identifies automatic
  validation support for consumers that must reject an incompatible package.
  This exported capability is not a setting or a package release version.

### Breaking changes

- Remove `SCHEMA_PROFILE`, `VALIDATION.MODE`, and `VALIDATION.SCHEMA` from
  settings. Their presence now raises a configuration error with migration
  guidance. `ValidationSettings` accepts safety limits only; validation cannot
  be disabled or replaced by a callback.
- `xmlschema` is a normal dependency. The old `[schema]` installation extra is
  retained as an empty compatibility alias; `[editor]` remains optional.
- Hyperview responses require complete, valid HXML. Body assignments, `write()`,
  and `writelines()` validate atomically; failed updates preserve the prior body.
  Empty document bodies are allowed only for 204, 205, and 304, which reject
  nonempty bodies. HEAD validates the document before suppressing its transport
  body; pass `request=request` to eager responses for this request context.
  Assemble incremental XML before assigning it.
- Django gzip transport is accepted only when it encodes the already-validated
  document exactly. Compressed input cannot introduce a new document or bypass
  validation. Remove `Content-Encoding` before replacing the document afterward.
- Adopt package and application configuration together after reviewing existing
  database overrides. Previously unchecked invalid output now fails validation;
  do not use seed commands, automatic repairs, or shared-cache flushes to migrate.

### Fixed

- The active completion catalog preserves attribute namespaces and offers custom
  behavior attributes only for the selected literal action. Namespaced standard
  attributes no longer collide with unqualified application attributes in that
  catalog; upstream inspection helpers retain their original output.
- Incompatible duplicate declarations are rejected consistently, including
  differences inherited from schema defaults and namespace bindings, rather than
  letting the order of external schemas change validation results.
- External elements with simple XSD types no longer crash completion or Admin
  validation catalogs. They expose no custom attributes; validation stays strict.

### Changed

- The optional Admin editor now runs **Format and Validate** before **Save**,
  **Save and add another**, and **Save and continue editing**. Errors block the
  write with inline diagnostics, warning-only results continue the selected
  action, and the server form repeats validation when JavaScript is bypassed.

## 0.1.0a15 — 2026-09-08

### Changed

- The optional Admin editor now exposes one **Format and Validate** action. It
  applies conservative formatting first and then validates the resulting Ace
  buffer. When formatting is unsafe, validation still runs against the unchanged
  source and reports both outcomes independently.

## 0.1.0a14 — 2026-09-08

### Added

- **Validate source** now checks statically visible HXML elements, attributes,
  required attributes, and literal enumerations against the selected XSD catalog,
  including project `EXTRA_SCHEMAS`. Dynamic values remain deferred to full
  rendered-response validation, and incomplete static analysis is reported as a
  warning rather than a false success.

## 0.1.0a13 — 2026-09-08

### Added

- **Validate source** in the optional Admin editor checks an unsaved template name
  and source without context, rendering, persistence, or revision changes. It
  reports safe source diagnostics and Django syntax line numbers when available.

### Removed

- The experimental static Admin template preview and its server-configured
  scenarios. Template authoring keeps the Ace editor, formatter, schema-backed
  completion, and publication validation.

## 0.1.0a12 — 2026-09-08

### Added

- Opt-in Admin template preview with configured example-data scenarios, unsaved
  draft rendering, structured diagnostics, and an isolated static web view.
  Preview is disabled by default, keeps the editor buffer unchanged, and never
  publishes a template.

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
