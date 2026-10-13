# Release and rollback

Publish one reviewed tag through the hosted release workflow. It validates the
tag against the project version, requires the tagged commit to be on `main`, runs
CI on that commit, builds the candidate once, publishes that exact distribution to
PyPI, creates the matching GitHub Release, and deploys its documentation only
after PyPI succeeds.

## Configure once

Before the first release:

1. In PyPI, create a **PyPI Trusted Publisher** for this repository, workflow
   `release.yml`, and GitHub environment `pypi`.
2. In GitHub, create the protected `pypi` environment. The workflow uses OIDC
   and does not use an API token or repository secret.
3. Select **GitHub Actions** as the Pages source and retain the
   `github-pages` environment used by GitHub Pages.
4. In GitHub, add a ruleset on `main` that requires status checks to pass before
   merging. Require `codecov/project`, `codecov/patch` and the CI jobs, named
   exactly as the pull request checks list shows them: `quality`,
   `compatibility (3.12, 5.2.17)`, `compatibility (3.12, 6.1.1)`,
   `compatibility (3.13, 5.2.17)`, `compatibility (3.13, 6.1.1)`,
   `compatibility (3.14, 5.2.17)`, `compatibility (3.14, 6.1.1)`, `redis` and
   `artifacts`. The release relies on this ruleset: a commit that reached `main`
   passed those checks, and the release only verifies that the tagged commit is on
   `main`. Without the ruleset nothing blocks a merge on a failed or missing Codecov
   status. After enabling it, open a documentation-only pull request and confirm
   that both Codecov statuses appear; if `codecov/patch` never reports on a change
   with no coverable lines, drop it from the required checks.
5. Install the Codecov GitHub App for the repository and store the upload token as
   the repository secret `CODECOV_TOKEN`. CI uploads with `fail_ci_if_error: true`,
   so a push or same-repository pull request fails without it.

PyPI and Pages jobs receive OIDC only within their own jobs.
Only Pages receives `pages: write`; validation and staging remain read-only.

## Compatibility review

For every beta, review the [compatibility commitment](api-reference.md#beta-compatibility-policy),
[verified scope](testing.md#verified-scope) and [current roadmap](roadmap.md).
Record incompatible changes, migrations and any security exception in the
changelog. A version bump or documented policy does not publish a release or
waive the gates below; tag creation and publication still require an explicit
release decision.

## Release order

1. Update `[project].version`, commit the lock if it changes, and merge to `main`
   through the ruleset above, after the checks in [Testing](testing.md) pass. The
   release SHA must be on `main`.
2. Tag that exact commit with the matching version, for example `v0.1.0`, and
   push the tag. Never reuse a tag.
3. The release workflow checks the tag against `pyproject.toml`, fetches `main`
   and requires the tagged commit to be one of its ancestors, runs reusable CI with
   Redis on that commit, and builds the distribution and site once. It uploads no
   coverage. A tag on a commit that is not on `main` fails before anything is built.
4. Staging validates the layout, records SHA-256 checksums, and separates the
   immutable distribution from the Pages artifact.
5. Trusted Publishing uploads the staged distribution. After PyPI succeeds, the
   workflow creates a GitHub Release with those same files and checksums. Alpha,
   beta, release-candidate, and development versions remain GitHub prereleases.
6. Pages starts only after the GitHub Release succeeds and deploys the
   already-built site. The documentation header reads the newest public release,
   including prereleases, so it shows the tag that produced the published site.

Tag a commit that is already on `main`; the tag may be older than the tip. The
check shows that the commit is on `main`, not that its checks passed: a commit
that bypassed the ruleset passes it. Never bypass a failed check or replace an
existing tag, and observe the hosted workflow after each release.

For a reviewable preview without publishing, run the manual documentation
preview workflow. The checked-in command remains:

```console
uv run zensical build --clean --strict -f zensical.yml
```

Generated `site/` content is disposable and must not be committed.

## Roll back safely

Pin the consumer to the previous known-good package version and redeploy it. If
the incident is limited to caching, remove the `CACHE` section or move to a new
namespace before serving traffic. Keep the old namespace until in-flight readers
finish.

For the optional database source, remove it from `SOURCES` before removing the
contrib app. Preserve its table until stored templates are exported or no longer
needed. A code rollback does not reverse a committed database publication, and a
failed post-commit cache callback does not roll the database back.

If a published version is unsafe, yank it through the PyPI project interface so
new resolvers avoid it, but preserve it for reproducibility. Stop or roll back
the Pages deployment independently if its content is wrong. Then revert the
fault on the development chain, add a regression test, increment the version,
and publish a new tag. Never replace an existing tag or PyPI artifact.


## Automatic validation adoption

Adopt the package and consumer configuration together. An upgrade now enforces
one corrected schema on every rendered document and fragment; there is no
validation toggle or alternate callback to postpone activation.

Before changing a served environment, validate a synthetic corpus in isolated
source checkouts that preserve current reviewed changes. Review effective database
overrides before filesystem fallbacks, using only explicitly authorized aliases
and a verified backup. The read-only integrity command checks identities, not
rendered XSD compatibility. This package does not repair or publish stored data
automatically; do not use a demo seed command as an upgrade procedure.

After human review, publish any necessary source repairs through the publication
services with the explicit alias and `expected_revision`. Concurrent edits require
another review. Pin only an available, approved release and repeat installed-package
acceptance before switching the running application. A consumer may require the
exported `HYPERVIEW_VALIDATION_CONTRACT="automatic-xsd-v1"` at startup to reject an
older package that ignores new extension declarations.

If adoption fails, retain or restore the reviewed package/configuration pair.
Data rollback requires its separate approved backup procedure, not automatic
replacement of current rows. Rotate only Hyperview's cache namespace when needed;
never flush a shared cache. Native visual and accessibility acceptance remains a
separate consumer responsibility, not a claim made by schema tests.
