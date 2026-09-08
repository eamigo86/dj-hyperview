# Hyperview schema provenance

These files are copied without modification from the `hyperview` npm package,
version 0.110.0, published by Instawork and licensed under the MIT License.

Source: https://github.com/Instawork/hyperview/tree/0.110.0/schema

| File | SHA-256 |
| --- | --- |
| `alert.xsd` | `3d3d951a7349eba2da068133934ad00c3ad86483441a442e9b4a64353fd532a4` |
| `core.xsd` | `fb5e6594acb7f8d54589f9528caca7902b8fef6447cc7d362ec6cd575a2bf9c3` |
| `hyperview.xsd` | `f4074a715b7fe4c3ce38396b7ae7bdfa03692ab9138c7d1afb8ae218fa39089a` |
| `scroll.xsd` | `f67ff1f62ba86aacd3b2d7d10d5bd9952972f6e55dd7200a5100f6012ced8c2d` |

The upstream MIT license is reproduced in `LICENSE.md` beside these schemas.

## Immutable r2 adaptation

`r2/hyperview.xsd` is package-authored, not an upstream replacement. It layers
only the audited declarations listed in `r2/manifest.json` over the unchanged
margin-only adaptation. `r2/catalog.json` is deterministic qualified-name
metadata generated from that schema. The manifest records SHA-256 hashes of both
resources; tests verify hashes and normalize the approved additions back to the
original declaration structures.

Evidence: the pinned Hyperview 0.110.0 `HvText`, `HvImage`, `HvTextField`, shared
modal and stylesheet converters; the consumer's pinned React Native 0.86.3 text,
image and accessibility converters. This establishes source-level support, not
native device E2E results. The platform and numeric-conversion limitations are
listed in the package configuration reference.

User registrations do not modify these resources. A trusted in-memory overlay
is generated from the closed typed registry; external overrides remain forbidden.
