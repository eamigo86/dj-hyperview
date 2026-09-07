# Changelog

This page records user-visible changes for each published version.

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
