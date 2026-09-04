# Installation

Install dj-hyperview into a supported Django project:

```bash
uv add dj-hyperview
```

The supported baseline is Python 3.12 or newer, below Python 3.15, with
Django 5.2 or 6.1.

Register the package app:

```python
INSTALLED_APPS = [
    # Your project apps.
    "dj_hyperview.apps.DjHyperviewConfig",
]
```

The base app performs no database, cache, or network access during startup.
Database-backed templates and admin integration remain optional.

Continue with [template configuration](configuration.md), or return to the
[documentation home](index.md).
