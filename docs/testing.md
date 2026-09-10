# Test a consumer integration

Keep representative XML fixtures in the consumer's test tree. The package's
reference project lives under `tests/consumer_project`; applications should use
an equivalent tests-only location. Exercise the same public resolver used in
production without copying fixtures into package code.

```python
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from dj_hyperview import TemplateResolver

FIXTURES = Path(__file__).parent / "fixtures" / "hyperview"


@override_settings(
    HYPERVIEW={
        "TEMPLATE_DIRS": [FIXTURES],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    }
)
class HyperviewResolutionTests(SimpleTestCase):
    def test_home_resolves_from_consumer_fixtures(self) -> None:
        resolved = TemplateResolver.from_settings().resolve("home.xml")

        self.assertEqual(resolved.name, "home.xml")
        self.assertIn("<view", resolved.content)
```

Add companion tests for empty content, misses, unsafe names, include/extends,
escaping, and the response media type. When database or cache profiles are
enabled, cover commit and rollback separately; use SQLite and LocMemCache unless
the deployment depends on another backend.

For `template_invalidated`, use `TransactionTestCase`/transactional pytest tests
to verify real commit, rollback, and each database alias. Ordinary `TestCase`
does not commit its outer transaction; `captureOnCommitCallbacks(execute=True)`
can exercise callback behavior but is not a real commit. Disconnect test receivers
afterward, and cover cache failure plus a failing receiver without losing the
original post-commit exception. No Redis is needed for this signal contract.

Package contributors can reproduce the supported aggregate coverage gate:

```console
uv run python -m tools.test_matrix --django-version 5.2.17
uv run python -m tools.test_matrix --django-version 6.1.1
```

Each compatibility cell runs the complete base suite followed by the optional
Admin profile, then requires **line coverage ≥95% and branch coverage ≥95%**
independently. The runner compares exact counters in the fresh `coverage.xml`,
not rounded percentages, and rejects missing or invalid reports. It also keeps
the combined 95% threshold. Use this runner rather than a plain `pytest` command
when checking the complete coverage contract.

The local default profile requires no Redis service or network. Live Redis
acceptance requires `--redis` and an explicit service URL. CI also runs the Redis
job on normal pushes and pull requests, alongside the six unchanged default
cells. Reusable and manually dispatched CI runs default to `redis: true`; a
caller can explicitly select `redis: false` for base-only verification. Keep
source/backend failures deterministic rather than using sleeps in transaction
or race tests.

### Current-run coverage uploads

CI retains the canonical Python 3.12/Django 6.1.1 report and, when selected, the
Redis report as separate artifacts. Each contains the original `coverage.xml`
and a manifest recording the checked-out SHA, repository, workflow run/attempt,
profile, Python/Django versions, report digest and exact coverage counters.

The producer records its start before testing. The canonical runner removes
old XML before executing either test phase; sealing runs only after success and
rejects timestamps predating that start. The collector downloads only artifacts
named for its current run/attempt. It validates both identities and digests,
checks source paths, rejects symlinks and unexpected files, and rechecks the
same independent ≥95% thresholds before supplying explicit XML paths to one
Codecov upload. No XML is rewritten or averaged. An explicit `redis: false`
caller supplies only the default report, not historical Redis evidence.

Missing, altered or failed-profile reports block the collector. All six base
cells still have to succeed; adding the Redis report does not replace their
no-Redis coverage gates. These manifests establish trusted CI producer
provenance, not an attestation against a compromised runner.

### Codecov acceptance, not just upload

**Both Codecov project and patch must approve the exact commit before release.**
The local independent ≥95% line/branch gates remain unchanged. Codecov uses its
own metric and automatic baseline targets: passing one gate does not imply
passing the other.

For the first assessment of a commit, ordinary CI:

1. Passes all six no-Redis cells and the selected Redis profile. A pre-job waits
   for older eligible producers before taking the per-SHA lock. It never waits
   for newer runs or for its own current attempt.
2. Verifies the current reports, uploads both actual XML files once, and waits
   until Codecov reports that exact uniquely named upload as **MERGED**.
3. Explicitly sends notifications. It then requires new successful
   `codecov/project` and `codecov/patch` statuses from the actual Codecov bot,
   posted after that notification boundary on the exact SHA. A missing status,
   including for an empty diff, is not inferred to be a pass.
4. Retains an immutable acceptance receipt for 30 days. A successful same-repo
   push run can supply an assessment to another push run with identical
   evidence. Same-repository PR receipts are limited to that same PR and merge
   SHA, not main or release. The PR's Codecov target may legitimately be a
   `/pull/N` URL; fork producers are not trusted receipt sources.

The receipt binds repository/SHA, each profile's Python/Django versions, the
complete canonical XML except the root timestamp, the versioned workflow/tool
and Codecov policy, the exact merged upload set, and accepted status IDs. It
is consumed only through a digest-checked pinned artifact download and an
independent recheck of the successful producer **attempt**, artifact identity
and expiry. The latest bot statuses and the current upload set must still match.
Re-running all jobs does not turn the latest mutable run state into proof of an
earlier attempt's success.

PRs have two different commit identities. Coverage and Codecov statuses use the
checked-out **merge SHA**, while workflow-run/artifact metadata uses the source
**head SHA**. CI passes the event's PR number, head and base explicitly; the
helper verifies that the merge commit's two raw parents are exactly that base
and head, including in a shallow checkout. Discovery queries the API by head,
but selects only the receipt named for the verified merge SHA. The receipt must
retain the same PR/head/base/merge tuple on reuse.

The API's `pull_requests` association may be empty. In that case the PR number
and base are tied through the trusted workflow event, verified commit parents
and immutable producer receipt, **not an invented API association**. If the API
supplies an association it must match, and workflow/attempt/repository/head and
artifact identities are checked independently. GitHub documents the PR merge
checkout in [workflow event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).

If evidence is identical, CI reuses the assessment without another upload or
notification. A branch-to-main fast-forward can promote the branch assessment
into a **new main receipt after main's own tests**. This retains one original
assessment reference and acceptance time; it does not nest receipt chains,
rewrite old artifacts, or renew the original artifact's lifetime. Release accepts
only the successful main producer's receipt, never a branch or PR directly.

The pre-job has a 20-minute bound outside the lock. Assessment/reuse has one
10-minute helper deadline inside the lock, within a 15-minute job bound. The
per-SHA queue does not cancel an in-flight assessment. No lock holder waits for
another unfinished producer. API reads have bounded pagination, size and request
timeouts; an unavailable or ambiguous response fails closed. A changed digest
is an error, not a warning.

`codecov.yml` sets `require_ci_to_pass: false`, `wait_for_ci: false` and
`manual_trigger: true` **only to control notification timing** and avoid making
Codecov wait for the CI job that is waiting for Codecov. It does not lower targets,
ignore failed checks or replace the mandatory CI prerequisites. See the
[Codecov YAML reference](https://docs.codecov.com/docs/codecovyml-reference) and
[explicit notification command](https://docs.codecov.com/docs/cli-options#send-notifications).

#### Missing proof and recovery

An upload alone, an old green status without a receipt, or a successful API call
is insufficient. Codecov can deduplicate identical assessments and never issue
new status IDs. If a valid receipt is unavailable, the bounded first-assessment
path can therefore fail even when old statuses are green. Do not manufacture
freshness, reupload in a loop, weaken thresholds or bypass the release check.
Investigate the failed stage and use a new reviewed commit when fresh evidence
is required; never move an old tag.

A receipt expires when GitHub expires/deletes its artifact, or when its original
assessment artifact expires. A newer promotion does not extend that eligibility.
Versioned policy changes invalidate the fingerprint. Mutable Codecov service
settings are **not** covered by that hash: after an external policy change,
explicitly revoke the affected acceptance artifacts before attempting reuse.
A compromised trusted runner or artifact service is outside this provenance
model.

Explicit `redis: false` remains a base-only CI mode and cannot create a release
receipt. Manual dispatch still runs its tests and artifact jobs and can reuse an
eligible identical push receipt. Without that proof its coverage job stops
**before upload**, directing the operator to ordinary push/main CI; it cannot
create an orphan first assessment. Missing or incompatible evidence must be
resolved through ordinary main CI, not by creating a tag. For publication
ordering and atomic main/tag pushes, see [Release and rollback](release-rollback.md).

The helper only reads fixed GitHub/Codecov APIs. GitHub reads use `contents: read`,
`actions: read` and `statuses: read`; upload authentication remains with the pinned
Codecov action. No status-writing permission is granted. The
[run-attempt API](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run-attempt)
and [artifact API](https://docs.github.com/en/rest/actions/artifacts) provide the
producer and immutable artifact metadata. Local contract tests inject API
responses and do not upload coverage or prove hosted service acceptance.

The contributor suite also executes the editor's dependency-free JavaScript
tests with Node 24:

```console
node --test tests/js/test_hxml_editor.mjs
```

CI smoke-tests the built wheel in three isolated profiles: no extras,
the deprecated empty `[schema]` alias, and optional `[editor]`. Wheel construction
remains a CI responsibility;
local quality checks do not need to build a distribution.

### Realtime transport profiles

The base suite tests immutable capture, rollback/alias behavior, a finite
publisher worker, ACK/queue/cancellation ports and actual Django ASGI lifecycle,
including sync-only middleware. It needs no Redis client or server; a fresh
subprocess asserts startup imports neither Redis nor a worker.

The existing Redis opt-in additionally runs
`tests/compat/test_realtime_redis.py` with `DJHV_TEST_REDIS=1` and an explicit
`DJHV_REDIS_URL`. Use a dedicated service or approved loopback test instance;
all test subscriptions use fresh UUID namespaces and never flush. One test
closes **only its own** Redis connection using CLIENT ID/KILL to verify that the
real PubSub client does not reconnect. Production ACLs do not need that test-only
permission. The profiles exercise redis-py 7.4.1 and 8.1.0 against the actual
service, and the canonical `tools.test_matrix --redis` appends both base/admin
coverage before independently checking line and branch thresholds. Unit-port
controls do not claim real Redis or native mobile proof.
