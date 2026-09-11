# Roadmap and release scope

Start with the [API compatibility commitment](api-reference.md#beta-compatibility-policy)
and [verified test scope](testing.md#verified-scope) before adopting a prerelease.
**0.1.0b1** begins the 0.1 beta line by formalizing the existing a22 contract and
its documented limits; it does not add new SSE runtime behavior. A source
candidate is not proof of publication: retain the existing
[CI, Codecov and publication gates](release-rollback.md) before adopting it.

## Available now

- Consumer-owned filesystem/database templates, ordered sources and an isolated
  Django template engine; no bundled application screens.
- Mandatory rendered XML/XSD validation, typed extension registration, standard
  Django CSRF and optional authorized Admin editing.
- Raw-template cache consistency, transactional publication services and
  after-commit hints, including supported QuerySet bulk operations.
- Optional Redis/SSE primitives, v1 compatibility and negotiated contextual v2
  metadata. An application must supply its authenticated endpoint and adapted
  client; package installation alone does not add mobile realtime behavior.

Use the [API reference](api-reference.md) for supported entry points,
[database guide](database-admin.md) for mutation semantics, and
[SSE guide](realtime.md) for coordinated backend/client adoption.

## Beta release checklist

1. Keep the documented compatibility commitment and tested scope aligned with
   the actual package; identify any intended breaking change and its migration.
2. Run the unchanged release gates on the exact candidate and retain a
   representative installed-package consumer acceptance result, with its
   database, client and platform scope stated explicitly.
3. Triage known reproducible defects affecting supported contracts. Passing
   tests or having no currently confirmed critical defect is not proof that
   all bugs are absent. A beta invites bounded real-world integration feedback.

This checklist adds no runtime setting or reduced quality gate.

## Backlog, not shipped promises

These are separate proposals or deployment qualification work, not requirements
to add every feature before beta. No delivery dates are promised.

| Topic | Current boundary and next useful step |
| --- | --- |
| Minimum client version and template capability selection | Applications can select existing named templates, but no automatic semantic-version selector or forced-update policy is provided. Define a consumer-owned compatibility contract before adding package APIs. |
| Feature flags and pilot cohorts | No built-in cohort service. Keep assignment/permissions server-owned and scope cache identity and SSE topics correctly; client headers are hints, not authority. |
| WebSockets, offline sync and durable replay | Not implemented. SSE invalidation does not become a command queue or full-document push protocol. Evaluate a separate delivery contract only when required. |
| Template history and rollback UI | Revisions detect conflicts; they do not preserve every past template. Design history and rollback independently from current publication services. |
| Additional deployment proof | Add PostgreSQL/MySQL, load/soak or native-platform acceptance only with explicit environments and reproducible tests; do not infer it from SQLite or XML tests. |
| Upstream mobile compatibility | Revalidate the candidates below against the chosen Hyperview release before reporting or changing dependencies. |

Raw SQL interception, global cross-template transactional snapshots, bundled
application UI and compatibility with the abandoned `django_hv` namespace are
not promised. Supported bulk publication is already implemented; it is no
longer a future milestone. Filesystem deploys still need explicit
[cache invalidation](cache-consistency.md#invalidate-after-a-filesystem-deploy);
there is no filesystem watcher or automatic deployment notification.

## Upstream follow-up inventory

These candidates preserve the earlier Hyperview 0.110.0 integration findings;
they are **not a fresh upstream-status check or confirmed package defects**.
For each, first reproduce with exact versions and minimal synthetic HXML, check
for duplicates, and report one cause at a time. Do not include private data.

| Candidate | Follow-up |
| --- | --- |
| Fragment load failure leaves loading active | Track [issue #1348](https://github.com/Instawork/hyperview/issues/1348) and [PR #1350](https://github.com/Instawork/hyperview/pull/1350); verify a published fix against the original case before claiming consumer acceptance. |
| Style IDs shadow update targets | Reproduce element/style ID collision; use distinct IDs in the consumer. |
| Switch change loses form context or submits an old value | Isolate the two symptoms with the current form value and request body. |
| Android ID overrides an accessible label | Verify actual TalkBack output with a minimal image/label case. |
| Upstream XSD differs from native attributes/types | Compare the same-version schema and native implementation, excluding custom app components. |
| Offline detection depends on one error shape | Separate Hyperview, Expo and React Native error classification from offline persistence. |
| Navigation/close/reload ambiguity | Reproduce one action and initial route stack at a time; distinguish API limits from defects. |
| Nested pressed opacity | Check existing [issue #318](https://github.com/Instawork/hyperview/issues/318) before opening a duplicate. |
| Default loading-indicator contrast | Measure effective colors and supported overrides before reporting an accessibility default. |
| Custom-behavior documentation and DOM APIs | Compare public APIs and documentation at the same upstream revision. |

Fragments not recompiling styles, platform safe areas/keyboard/gestures, and
application-specific components must not be relabeled upstream bugs without
an established contract and a reproduction.

## History and maintenance

The [changelog](changelog.md) and [Git history](https://github.com/eamigo86/dj-hyperview/commits/main/)
record released, versioned work. Superseded local planning snapshots were not
tracked in Git; they are not a source of current compatibility claims. Their
remaining actionable requirements are consolidated above rather than retained
as parallel status/specification/task documents.

Keep operational details in their canonical guides and update this page only
when the current scope or backlog changes. Implementation, regression tests and
the affected guide remain one reviewable work unit.
