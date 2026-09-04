# Hyperview 0.110.0 compatibility

`dj-hyperview` produces HXML and HTTP responses checked against a focused,
test-only contract for Hyperview 0.110.0. The contract covers the official
`https://hyperview.org/hyperview` namespace, full documents, independent
fragments, form behaviors and references, Django CSRF, the Hyperview media type,
and UTF-8 output.

The version and provenance are pinned in
`tests/contracts/hyperview/0.110.0/manifest.json` from the official
[Hyperview 0.110.0 npm release](https://www.npmjs.com/package/hyperview/v/0.110.0),
the [Instawork Hyperview repository](https://github.com/Instawork/hyperview),
and [Hyperview documentation](https://hyperview.org).

## Limits

This verifies the HXML and HTTP contract produced by the Django backend. It
does not execute the Hyperview client and is not a mobile, React Native, Expo,
or binary-compatibility test. The focused test-only contract covers only the
synthetic fixtures committed under `tests/`; it is not the complete upstream
schema and no fixture is shipped as runtime UI.
