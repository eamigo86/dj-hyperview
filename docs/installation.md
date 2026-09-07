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
    "dj_hyperview",
]
```

The base app performs no database, cache, or network access during startup.
Database-backed templates and admin integration remain optional.

## Optional authoring profiles

Install server-side Hyperview XSD 1.1 validation without changing the admin:

```bash
uv add "dj-hyperview[schema]"
```

Install validation and the HXML-aware Django Admin editor together:

```bash
uv add "dj-hyperview[editor]"
```

The base installation never imports either optional dependency. The editor is
also opt-in at runtime: add `"django_ace"` to `INSTALLED_APPS` and set
`HYPERVIEW["ADMIN"]["EDITOR"]` to `True`. Adding the package extra alone does
not change existing admin forms.

Continue with the [Quick Start](quickstart.md) to serve a filesystem-backed
screen, or open the complete [configuration reference](configuration.md).
