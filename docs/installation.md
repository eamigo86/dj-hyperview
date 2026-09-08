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

## Automatic schema validation

`xmlschema` is a normal dependency of the base package. Every rendered Hyperview
document and fragment uses the same corrected XSD 1.1 registry automatically.
No schema profile, validation mode, or callback needs to be configured.
The deprecated `[schema]` installation extra is an empty compatibility alias:
it does not enable a second validation mode or install an additional package.

## Optional Admin editor

```bash
uv add "dj-hyperview[editor]"
```

The extra adds Ace, not a different schema. The base package does not import
`django_ace`. Enable the editor by adding `"django_ace"` to `INSTALLED_APPS` and
setting `HYPERVIEW["ADMIN"]["EDITOR"]` to `True`. Installing the extra alone does
not change existing admin forms.

An upgrade changes validation for all rendered HXML. Review effective database
overrides as well as filesystem templates before adoption; follow the
[coordinated upgrade procedure](release-rollback.md#automatic-validation-adoption).

Continue with the [Quick Start](quickstart.md) to serve a filesystem-backed
screen, or open the complete [configuration reference](configuration.md).
