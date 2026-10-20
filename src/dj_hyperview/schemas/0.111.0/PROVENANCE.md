# Hyperview 0.111.0 schema provenance

The four root XSDs are copied byte-for-byte from the public
[`hyperview@0.111.0` npm tarball](https://registry.npmjs.org/hyperview/-/hyperview-0.111.0.tgz).
`PROVENANCE.json` records the tarball and per-file SHA-256 hashes. The package
identifies its license as MIT; `LICENSE.md` reproduces the upstream license.

Compared with 0.110.0, upstream adds only optional boolean `content-insets` to
its shared `scrollAttributes` group. The 0.110.0 directory is an unchanged
historical resource set, and the public upstream-inspection helper still points
to that archive. This active 0.111.0 validation contract is separate.

`compatibility/hyperview.xsd` carries the prior audited margin adaptation over
the 0.111.0 upstream root. `r4/hyperview.xsd` carries the prior audited native
corrections over that adaptation, and flat equivalents of the `viewChildren` and
`nonListViewChildren` groups of `core.xsd` that accept the same children.
`r4/manifest.json` identifies and hashes this new revision; `r4/catalog.json` is
generated from that corrected schema and is byte-identical to the `r3` catalog.
Neither overlay replaces an upstream file. Project registrations are generated
in memory, and external overrides remain forbidden.
