# Release and rollback

Release only a commit that passes both supported Django matrices, package
boundary checks, migrations, documentation checks, and the exact dependency
lock. The release tag must identify that reviewed commit.

## Release order

1. Run the commands in [Testing](testing.md) and `uv lock --check --offline`.
2. Let CI build and inspect the distribution; do not publish an unverified local
   artifact.
3. Publish the immutable artifact to PyPI with Trusted Publishing.
4. Deploy the documentation generated from that same tag only after PyPI
   publication succeeds.

The CI or an intentional manual preview must select the checked-in Zensical
configuration explicitly:

```console
uv run zensical build --clean -f zensical.yml
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

After containment, revert the release commit on the development chain, add a
regression test, and publish a new version. Never replace an existing tag or PyPI
artifact.
