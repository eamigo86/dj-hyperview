# Quick Start

Serve a consumer-owned Hyperview screen from a Django URL without adding XML
to the package itself.

## 1. Register the package

Install dj-hyperview, then add its Django application:

```python
INSTALLED_APPS = [
    "dj_hyperview",
]
```

## 2. Create the screen in your project

Create `hyperview/screens/home.xml` beside your Django project:

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

## 3. Configure the filesystem source

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

## 4. Expose the screen

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

Request `/hyperview/home/`. Django returns the rendered document using
`application/vnd.hyperview+xml`.

The screen remains owned by your project. Next, choose
[filesystem behavior](filesystem.md), [database publication](database-admin.md),
or the complete [configuration reference](configuration.md).
