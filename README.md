# dj-hyperview

![CI](https://img.shields.io/github/actions/workflow/status/eamigo86/dj-hyperview/ci.yml?branch=main&label=CI)
[![Codecov](https://codecov.io/gh/eamigo86/dj-hyperview/graph/badge.svg)](https://codecov.io/gh/eamigo86/dj-hyperview)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/dj-hyperview)
![Django Versions](https://img.shields.io/pypi/frameworkversions/django/dj-hyperview?label=django&color=0C4B33)
![PyPI](https://img.shields.io/pypi/v/dj-hyperview?color=blue)
![Downloads](https://img.shields.io/pepy/dt/dj-hyperview)
![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)
[![License](https://img.shields.io/pypi/l/dj-hyperview)](https://github.com/eamigo86/dj-hyperview/blob/main/LICENSE)

**Server-driven [Hyperview](https://hyperview.org/) UI for Django, with every
screen owned by your project.** Resolve, render, validate, cache, and publish
mobile UI from filesystem or database templates without bundling application
screens into the package.

- **Consumer-owned screens** — keep XML and HXML inside the Django project that
  defines the mobile application.
- **Ordered template sources** — resolve from configured filesystem directories,
  the optional database app, or custom source backends with explicit precedence.
- **Live database publication** — edit validated templates through Django admin
  and expose committed changes on the next request.
- **Cache consistency** — opt into any Django cache backend, including Redis,
  with source-aware keys and generation-based invalidation.
- **Django-native HTTP integration** — use lazy template responses, class-based
  views, request metadata, content negotiation, and standard CSRF protection.
- **Fail-closed validation** — enforce canonical names, UTF-8, XML safety,
  consumer-provided single-file XSD 1.0 rules, and configurable resource limits.

> dj-hyperview does not ship application screens, runtime XML or HXML files,
> mobile components, Redis, or a required database app. Those choices remain
> under the consumer project's control.

The upstream Hyperview schema uses composition and XSD 1.1 features. Flatten
and downgrade it before configuring it as the package validation schema.

## Requirements

- **Python:** 3.12, 3.13, or 3.14
- **Django:** 5.2 or 6.1
- **lxml:** 6.1 or newer, below 7
- **Hyperview contract:** 0.110.0

## Installation

```bash
# uv (recommended)
uv add dj-hyperview
```

```bash
# pip
pip install dj-hyperview
```

Register the base Django application:

```python
INSTALLED_APPS = [
    # Your project applications.
    "dj_hyperview",
]
```

The base app performs no database, cache, or network access during startup.
Database-backed templates and admin integration are optional.

## Quick start

Create `hyperview/screens/home.xml` inside the consumer project:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<doc xmlns="https://hyperview.org/hyperview">
  <screen id="home">
    <body>
      <view>
        <text>Hello from Django</text>
      </view>
    </body>
  </screen>
</doc>
```

Configure the filesystem source in `settings.py`:

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

Expose the screen through Django's URL configuration:

```python
from django.urls import path

from dj_hyperview import HyperviewTemplateView

urlpatterns = [
    path(
        "hyperview/home/",
        HyperviewTemplateView.as_view(template_name="screens/home.xml"),
        name="hyperview-home",
    ),
]
```

A request to `/hyperview/home/` returns the rendered document as
`application/vnd.hyperview+xml`.

## Configuration

All package settings live under the `HYPERVIEW` dictionary. Sources are queried
in declaration order, and the first match wins:

```python
HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "hyperview"],
    "SOURCES": [
        {"BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource"},
        {"BACKEND": "dj_hyperview.sources.FileSystemSource"},
    ],
}
```

To publish templates through the database and Django admin, add the optional app
and run migrations:

```python
INSTALLED_APPS = [
    # Django applications used by your project.
    "django.contrib.admin",
    "dj_hyperview",
    "dj_hyperview.contrib.database",
]
```

```bash
python manage.py migrate
```

Caching remains disabled unless a non-empty `HYPERVIEW["CACHE"]` configuration
is supplied. See the configuration and cache guides before enabling a shared
backend.

## Documentation

📚 **[Full documentation](https://eamigo86.github.io/dj-hyperview/)** — including
[Installation](https://eamigo86.github.io/dj-hyperview/installation/),
[Quick Start](https://eamigo86.github.io/dj-hyperview/quickstart/),
[Configuration](https://eamigo86.github.io/dj-hyperview/configuration/),
[Filesystem sources](https://eamigo86.github.io/dj-hyperview/filesystem/),
[Database and admin](https://eamigo86.github.io/dj-hyperview/database-admin/),
[Cache consistency](https://eamigo86.github.io/dj-hyperview/cache-consistency/),
[Security](https://eamigo86.github.io/dj-hyperview/security/), and the
[public Python API](https://eamigo86.github.io/dj-hyperview/api-reference/).

The package is published at
**[PyPI](https://pypi.org/project/dj-hyperview/)**.

## Development

See the [contributing guide](https://eamigo86.github.io/dj-hyperview/contributing/)
for the quality contract, supported test matrix, and release workflow.

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## License

dj-hyperview is distributed under the
[MIT License](https://github.com/eamigo86/dj-hyperview/blob/main/LICENSE).
