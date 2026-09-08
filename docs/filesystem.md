# Filesystem sources

Resolve consumer-owned templates from project directories without enabling a
database or cache. This is the smallest production setup.

## Configure one source

Create a directory in your Django project, then point `HYPERVIEW` at it:

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

Keep every XML or HXML file in the consumer project. The package ships no
application screens. Earlier directories take precedence when more than one is
configured.

## Resolve a template

Use a canonical, relative POSIX name:

```python
from dj_hyperview import TemplateResolver

resolved = TemplateResolver.from_settings().resolve("screens/home.xml")
content = resolved.content
origin = resolved.origin
```

An empty file is a valid hit. If all sources miss, resolution raises
`TemplateNotFound`. Django include/extends tags use the same resolver, directory
order, and canonical names as the root template.

## Keep names inside the roots

Template names cannot be absolute or contain empty segments, dot segments,
backslashes, control characters, isolated surrogates, or NUL bytes. Filesystem
sources also reject symlinks that escape a configured root. Public errors do not
echo rejected names or filesystem paths.

Long-lived source instances resolve their configured roots for every lookup, so
atomic root symlink changes are visible without reconstructing the source. A
missing path, a platform path-length limit, or a symlink loop is treated as a
normal miss. Missing configured roots also emit the non-blocking
`dj_hyperview.W006` system-check warning. Other access failures raise the
redacted `SourceUnavailable` contract, while non-UTF-8 files raise
`TemplateValidationError` with the stable `invalid_encoding` code.

Permission and I/O errors are failures, not misses, on every supported Python
version, including 3.14. If the first root contains an inaccessible template,
resolution stops with `SourceUnavailable` rather than silently serving a
lower-priority version. Check directory search permissions as well as file read
permissions when diagnosing deployment access failures.

Root symlink changes affect uncached lookups immediately. Cached templates stay
unchanged until their TTL, explicit invalidation, or a cache namespace rotation.
See [cache consistency](cache-consistency.md) before a filesystem deploy.

Add [database publication](database-admin.md) only when project editors need a
stored source.
