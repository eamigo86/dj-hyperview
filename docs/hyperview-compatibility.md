# Hyperview compatibility

dj-hyperview validates every rendered document and fragment against one schema
that ships inside the package. The Hyperview client version installed in your
mobile app does not change that schema.

| dj-hyperview | Active validation contract | Runtime schema |
| --- | --- | --- |
| 0.1.0b3 (current) | Hyperview 0.111.0 | Upstream 0.111.0 XSDs plus package-owned corrections, revision `r3` |
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
them: a margin adaptation that accepts signed decimal percentages, and the `r3`
corrections for text, image, accessibility, `date-field` and `style@width`
attributes. [Corrected schema boundaries](configuration.md#corrected-schema-boundaries)
lists every addition and states what the corrections do not permit. Project
elements and behaviors are registered separately with
[custom schemas](custom-schemas.md).

The repository records where the files came from. `PROVENANCE.json` holds the
tarball digest and a SHA-256 for each upstream XSD, and the `r3` manifest holds
the digests of the corrected entry point and catalog. The test suite verifies
them; the package does not re-check them at runtime.

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
- The repository records provenance for Hyperview 0.110.0 and 0.111.0 only. It
  makes no statement about later Hyperview releases; compare their XSDs with the
  shipped files before relying on them.
- The reference mobile matrix in [Mobile Getting Started](mobile-getting-started.md)
  is a tested example, not a compatibility range.
