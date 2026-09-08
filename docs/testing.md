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
the deprecated empty `[schema]` alias, and optional `[editor]`. Wheel construction
remains a CI responsibility;
local quality checks do not need to build a distribution.
