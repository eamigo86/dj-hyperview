# Changelog

This page records user-visible changes for each published version.

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

### Compatibility

- Tested against Django 5.2 and Django 6.1 on supported Python versions.
- Fragment update endpoints must return a bare client-safe element. Full HXML
  documents continue to use `application/vnd.hyperview+xml`.

