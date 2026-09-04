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
`TemplateNotFound`. Includes and extensions use the same resolver, directory
order, and canonical names as the root template.

## Keep names inside the roots

Template names cannot be absolute or contain empty segments, dot segments,
backslashes, control characters, isolated surrogates, or NUL bytes. Filesystem
sources also reject symlinks that escape a configured root. Public errors do not
echo rejected names or filesystem paths.

Add [database publication](database-admin.md) only when project editors need a
stored source.
