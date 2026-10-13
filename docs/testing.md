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

## Verified scope

Use this table to decide which deployment-specific checks you must add. It
summarizes the package's configured acceptance profiles, not a guarantee that
any combination outside them works. Exact targets live in `pyproject.toml` and
CI; dependency ranges alone are not a test result.

| Contract | Verified baseline | What it establishes |
| --- | --- | --- |
| Python | `3.12`, `3.13`, `3.14` | Six Python/Django compatibility cells, with deprecations treated as errors. |
| Django | `5.2.17`, `6.1.1` | The base suite and separate optional database/Admin profile in each cell. |
| Database engines | `django.db.backends.sqlite3` | Isolated in-memory default/replica databases, alias routing, commit/rollback and publication controls. |
| Editor JavaScript | `24` | Node tests for the package's editor and documentation JavaScript, not a native app runner. |
| Redis client | `8.1.0` | The scoped real-Redis transport profile, which installs the locked `redis` group, plus deterministic failure/lifecycle controls. `7.4.1` is the declared floor of the `[realtime]` extra and is not run against a live service. |

The default profile does not require Redis. The Redis profile adds an actual
service with unique test namespaces; it does not replace the default profile.
The current CI service image is declared in the workflow, not a promise to
support every Redis server topology or version.

**Not established by this matrix:** PostgreSQL or MySQL behavior, production
load/soak capacity, arbitrary cache backends, or universal iOS/Android/Expo
compatibility. SQLite tests do not establish another database's locking or
isolation behavior. Qualify the database, cache/Redis topology, ASGI server and
proxy used by your deployment, and test your own authentication/authorization,
CSRF, forms, component registration and native lifecycle. Consumer test results
must identify their exact package/client/platform versions separately.

XSD acceptance proves the checked XML/schema contract, not that a native
component or route is registered, a JSON-valued custom attribute passes its
application validator, or the resulting screen is usable. Static Admin checks
cannot substitute for rendering representative contexts. Use the public
[validation and response APIs](api-reference.md) and the
[custom-schema recipe](custom-schemas.md) in consumer tests.

SSE carries best-effort invalidation hints, not authoritative content. Delivery
is at-most-once, with no durable replay guarantee. Queue overflow/reconnect can
require a conservative resync and an authenticated HTTP reload; publish return
is not a delivery or layout acknowledgement. Auth, private topics, admission
limits and safe mobile refresh remain consumer responsibilities. See
[realtime profiles](#realtime-transport-profiles) and [security](security.md).

## Run the package quality gates

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

### Coverage in CI

The canonical Python 3.12/Django 6.1.1 cell and the Redis job each run
`tools.test_matrix`, which writes `coverage.xml`, and upload it to Codecov with the
pinned `codecov/codecov-action`. The action runs with `fail_ci_if_error`, so a lost
upload fails its job instead of leaving a green run with nothing to judge. Codecov
merges the two reports of a commit; an explicit `redis: false` caller uploads only
the default one. A release run on a tag uploads nothing, because the tagged commit
already got its statuses on `main`.

Two gates apply, and passing one does not imply passing the other:

- `tools.test_matrix` enforces the local ≥95% line and branch thresholds in every
  compatibility cell.
- Codecov posts `codecov/project` and `codecov/patch` on the commit, from its own
  automatic baseline targets and without a repository configuration file. By default
  it notifies only after the commit's other checks have passed, so a failing job
  leaves these statuses missing.

The repository does not poll those statuses; GitHub enforces them. Require them,
together with the CI jobs, in a ruleset on `main` as described in
[Configure once](release-rollback.md#configure-once). Until that ruleset exists,
nothing blocks a merge on a failed Codecov status. A pull request that GitHub runs
without the repository secret, from a fork or by Dependabot, can upload only if
Codecov accepts tokenless uploads for the repository; otherwise its upload step
fails.

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
permission. The profile exercises redis-py 8.1.0, the version in the locked
`redis` dependency group, against the actual service; the `[realtime]` extra
allows `>=7.4.1,<9`, but no run installs the 7.4.1 floor. The canonical
`tools.test_matrix --redis` appends both base/admin
coverage before independently checking line and branch thresholds. Unit-port
controls do not claim real Redis or native mobile proof.

## Hyperview 0.110.0 compatibility

`dj-hyperview` produces HXML and HTTP responses checked against a focused,
test-only contract for Hyperview 0.110.0. The contract covers the official
`https://hyperview.org/hyperview` namespace, full documents, independent
fragments, form behaviors and references, Django CSRF, the Hyperview media type,
and UTF-8 output.

The [version manifest](https://github.com/eamigo86/dj-hyperview/blob/main/tests/contracts/hyperview/0.110.0/manifest.json)
pins the official
[Hyperview 0.110.0 npm release](https://www.npmjs.com/package/hyperview/v/0.110.0),
the [official Hyperview repository](https://github.com/Instawork/hyperview),
repository tag `v0.110.0`, upstream commit
`f715ae5cdf07733a4b846d7744518e42dff40407`, artifact hashes, and relevant
[Hyperview documentation](https://hyperview.org).

### Validated boundary

- Response bytes and XML declarations must agree on strict UTF-8.
- DTD and entity declarations are rejected before schema validation.
- A focused XSD validates shape; a separate check enforces exact ID references.
- Full, fragment and form responses exercise media type, status, escaping and CSRF.

### Limits

This verifies the HXML and HTTP contract produced by the Django backend. It
does not execute the Hyperview client and is not a mobile, React Native, Expo,
or binary-compatibility test. The focused test-only contract covers only the
synthetic fixtures committed under `tests/`; it is not the complete upstream
schema and no fixture is shipped as runtime UI.
