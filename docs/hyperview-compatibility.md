# Hyperview compatibility

dj-hyperview validates every rendered document and fragment against one schema
that ships inside the package. The Hyperview client version installed in your
mobile app does not change that schema.

| dj-hyperview | Active validation contract | Runtime schema |
| --- | --- | --- |
| 0.1.0b4 (current) | Hyperview 0.111.0 | Upstream 0.111.0 XSDs plus package-owned corrections, revision `r4` |
| 0.1.0b3 | Hyperview 0.111.0 | Upstream 0.111.0 XSDs plus package-owned corrections, revision `r3` |
| 0.1.0b2 | Hyperview 0.111.0 | Upstream 0.111.0 XSDs plus package-owned corrections, revision `r3` |
| 0.1.0b1 | Hyperview 0.110.0 | Upstream 0.110.0 XSDs plus package-owned corrections, revision `r2` |

`dj_hyperview.HYPERVIEW_SCHEMA_VERSION` reports the active version, and
`dj_hyperview.schema.get_hyperview_catalog()` returns the matching completion
catalog (`catalog_format: 2`). See the [API reference](api-reference.md#validation-and-cache).

## The current contract: 0.111.0

The four root XSDs (`alert.xsd`, `core.xsd`, `hyperview.xsd`, `scroll.xsd`) are
copied byte for byte from the public `hyperview@0.111.0` npm tarball. Compared
with 0.110.0, upstream adds only an optional boolean `content-insets` attribute
to its shared scroll attributes, which the scroll-capable elements accept.

Two package-owned layers sit on top of the upstream files and replace none of
them: a margin adaptation that accepts signed decimal percentages, and the `r4`
corrections for text, image, accessibility, `date-field` and `style@width`
attributes. [Corrected schema boundaries](configuration.md#corrected-schema-boundaries)
lists every addition and states what the corrections do not permit. Project
elements and behaviors are registered separately with
[custom schemas](custom-schemas.md).

`r4` also replaces the nested child groups of `body`, `header`, `view`, `form`,
`select-single`, `select-multiple`, `item` and `section-title` with flat groups
that accept the same children, the same `##other` wildcard and the same
repetition, and list the most common elements first so validation reaches them
sooner. A test compares each flat group with the upstream groups on every run.

The repository records where the files came from. `PROVENANCE.json` holds the
tarball digest and a SHA-256 for each upstream XSD, and the `r4` manifest holds
the digests of the corrected entry point and catalog. The test suite verifies
them; the package does not re-check them at runtime.

## Hyperview 0.113.0 to 0.115.1 clients

The four original XSDs published with `hyperview@0.113.0`, `hyperview@0.114.0`,
`hyperview@0.115.0` and `hyperview@0.115.1` are byte-identical to the upstream
0.111.0 and 0.112.0 files. No new schema asset, corrected `r4` revision, or
dj-hyperview version is needed for any of these client releases. The 0.114.0,
0.115.0 and 0.115.1 source changes touch no request header, fragment handling or
content type that dj-hyperview relies on. This comparison covers the XML contract
only:

- Hyperview 0.113.0 requires React Navigation 7 in the mobile host, and its option
  component now exposes the existing `selected` attribute as an accessibility
  state.
- Hyperview 0.114.0 adds `@react-navigation/native-stack` as a peer dependency and
  renders stacks with it on iOS (Android and web keep the JavaScript stack). It also
  changes how `close`, `back` and `navigate` behave from routes that are not
  focused, refreshes render-time behaviors of `hyper-ref` elements, and fires a
  navigator's `load` behaviors once per element.
- Hyperview 0.115.0 changes no dependency. On iOS it gives modal transitions a
  350 ms animation, and closing a modal that is not the focused route now removes
  that modal and the routes it opened while keeping unrelated routes above it.
- Hyperview 0.115.1 changes no dependency. It ignores a `back` request when the
  navigator has no state yet, and no longer fails on such a navigator when it
  handles `close` or records which route opened another.

Client navigation and native behavior need their own tests and device acceptance;
server-side schema validation cannot prove either.

## History: 0.110.0

The upstream 0.110.0 XSDs remain in the package unchanged, together with the
config-free helpers `get_hyperview_schema_path()` and `build_hyperview_catalog()`,
which still inspect that archive rather than the active registry. Its package-owned
`r2` corrections, margin adaptation and committed catalog are no longer shipped.
The focused, test-only contract that exercises the HTTP and HXML boundary was
written against 0.110.0 and is described in
[Testing integrations](testing.md#hyperview-01100-compatibility).

## What this page does not claim

- The package never runs the Hyperview client, so schema acceptance does not prove
  that a native component, behavior or route exists in your app.
- The repository records bundled provenance for Hyperview 0.110.0 and 0.111.0
  only. The 0.113.0 to 0.115.1 comparison above does not bundle new files or
  establish a blanket compatibility range for future releases.
- The reference mobile matrix in [Mobile Getting Started](mobile-getting-started.md)
  is the matrix HyperTodo 1.3.3 runs, a tested example and not a compatibility range.
