# Configuration

Point dj-hyperview at templates owned by your Django project:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "SOURCES": [
        {"BACKEND": "dj_hyperview.sources.FileSystemSource"},
    ],
}
```

Resolve a canonical relative POSIX name through the public API:

```python
from dj_hyperview import TemplateResolver

template = TemplateResolver.from_settings().resolve("screens/home.xml")
```

Sources are queried in order and the first hit wins. Absolute paths, traversal,
backslashes, empty path segments, control characters, and surrogates are
rejected. Omitting `CACHE` keeps caching disabled and avoids cache backend
initialization.

Continue with [Filesystem](filesystem.md), [Database and admin](database-admin.md),
[Cache consistency](cache-consistency.md), [Security](security.md),
[Testing](testing.md), and [Release and rollback](release-rollback.md). Return
to the [documentation home](index.md).
