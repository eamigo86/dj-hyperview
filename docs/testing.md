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

The default profile requires no Redis service or network. Live Redis acceptance
is explicit and opt-in. Keep source/backend failures deterministic rather than
using sleeps in transaction or race tests.

The contributor suite also executes the editor's dependency-free JavaScript
tests with Node 24:

```console
node --test tests/js/test_hxml_editor.mjs
```

CI smoke-tests the built wheel in three isolated profiles: no extras,
`[schema]`, and `[editor]`. Wheel construction remains a CI responsibility;
local quality checks do not need to build a distribution.

## Chromium preview acceptance

The opt-in browser profile tests the real Admin, Ace, CSRF-protected preview POST,
and sandboxed static renderer against a temporary test database and local test
server. It never uses a consumer deployment or HyperTodo.

```bash
uv sync --locked --group browser --no-build
uv run --locked --group browser python -m playwright install chromium
DJHV_TEST_BROWSER=1 uv run --locked --group browser python -m pytest -q \
  --ds=tests.settings_browser tests/browser
```

The separate development-only `browser` group pins Playwright 1.60.0 and its
matching Chromium revision for reproducibility, not as a latest-version claim.
CI installs the matching browser and Linux prerequisites with
`python -m playwright install --with-deps chromium`. Neither Playwright nor its
browser is a package runtime dependency. Ordinary Python matrix cells do not
launch browsers; the dedicated CI browser job is required before artifact jobs.

Acceptance covers real context selection, immutable editor content/selection/undo,
source versus rendered diagnostics, stale responses, media/action suppression,
iframe isolation, and desktop/mobile Admin themes. To capture the desktop dark
workspace during the test, additionally set `DJHV_BROWSER_SCREENSHOT` to an
absolute PNG path in a temporary directory. Browser tests do not build assets or
publish templates.

The browser baseline uses Django Admin's normal test settings. A consumer's
stricter host CSP may independently block frames or generated inline styles;
iframe sandboxing does not override inherited CSP. Do not relax the site's
global policy merely to enable this approximation. Assess any narrowly scoped
Admin policy adjustment separately, or keep the escaped HXML diagnostics only.
