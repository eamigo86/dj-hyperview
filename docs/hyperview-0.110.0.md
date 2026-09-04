# Hyperview 0.110.0 compatibility

`dj-hyperview` produces HXML and HTTP responses checked against a focused,
test-only contract for Hyperview 0.110.0. The contract covers the official
`https://hyperview.org/hyperview` namespace, full documents, independent
fragments, form behaviors and references, Django CSRF, the Hyperview media type,
and UTF-8 output.

The [version manifest](../tests/contracts/hyperview/0.110.0/manifest.json)
pins the official
[Hyperview 0.110.0 npm release](https://www.npmjs.com/package/hyperview/v/0.110.0),
the [official Hyperview repository](https://github.com/Instawork/hyperview),
repository tag `v0.110.0`, upstream commit
`f715ae5cdf07733a4b846d7744518e42dff40407`, artifact hashes, and relevant
[Hyperview documentation](https://hyperview.org).

## Validated boundary

- Response bytes and XML declarations must agree on strict UTF-8.
- DTD and entity declarations are rejected before schema validation.
- A focused XSD validates shape; a separate check enforces exact ID references.
- Full, fragment and form responses exercise media type, status, escaping and CSRF.

## Limits

This verifies the HXML and HTTP contract produced by the Django backend. It
does not execute the Hyperview client and is not a mobile, React Native, Expo,
or binary-compatibility test. The focused test-only contract covers only the
synthetic fixtures committed under `tests/`; it is not the complete upstream
schema and no fixture is shipped as runtime UI.
