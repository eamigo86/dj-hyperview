# Release and rollback

Publish one reviewed tag through the hosted release workflow. It validates the
tag, builds the candidate once through CI, publishes that exact distribution to
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

PyPI and Pages jobs receive OIDC only within their own jobs.
Only Pages receives `pages: write`; validation and staging remain read-only.

## Release order

1. Update `[project].version`, commit the lock if it changes, and merge only
   after the checks in [Testing](testing.md) pass.
2. Tag that exact commit with the matching version, for example `v0.1.0`, and
   push the tag. Never reuse a tag.
3. The release workflow checks the tag against `pyproject.toml`, runs reusable
   CI with Redis, and builds the distribution and site once.
4. Staging validates the layout, records SHA-256 checksums, and separates the
   immutable distribution from the Pages artifact.
5. Trusted Publishing uploads the staged distribution. After PyPI succeeds, the
   workflow creates a GitHub Release with those same files and checksums. Alpha,
   beta, release-candidate, and development versions remain GitHub prereleases.
6. Pages starts only after the GitHub Release succeeds and deploys the
   already-built site. The documentation header reads the newest public release,
   including prereleases, so it shows the tag that produced the published site.

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
