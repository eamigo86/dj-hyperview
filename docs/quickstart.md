# Quick Start

Serve a consumer-owned Hyperview screen from a Django URL without adding XML
to the package itself.

## Request lifecycle

**Django renders the markup; Hyperview renders the native interface.**
HXML is Hyperview's XML vocabulary for screens and interactions, not HTML in a
WebView. dj-hyperview runs on the server; the separate Hyperview React Native
client runs in your mobile app.

```text
Phone: Hyperview requests a screen URL
  → Django middleware and URL routing
  → Your view: permissions, application data, template context
  → dj-hyperview: resolve template → render with Django → validate final HXML
  → HTTP response containing HXML
Phone: Hyperview displays native components
  → A tap with an HTTP behavior starts another request
```

1. The client makes an HTTP request to its configured `entrypointUrl`.
2. Django handles it through its normal middleware, URL resolver and view.
   Your application still owns authentication, authorization and database queries.
3. Your view returns a `HyperviewTemplateResponse`, directly or through
   `HyperviewTemplateView`. Like Django's `TemplateResponse`, it is rendered
   after the view returns, before the response is sent.
4. dj-hyperview resolves the template using `HYPERVIEW["SOURCES"]` in order.
   With caching enabled, a valid cached **template source** can avoid a source
   read. This is not a cache of a user's rendered screen. Django template syntax
   is evaluated with the context and request; the resulting XML is automatically
   validated against the corrected schema and resource limits. Invalid output
   raises `TemplateValidationError` instead of being served as a successful screen.
5. Django sends the HXML response. Hyperview reads it and creates native UI.
   Later, an HTTP behavior supplies the URL, method and action for another
   round trip. A navigation action can load a full document; `replace` can
   update a single target with a fragment, as shown below.

You do not need SSE for this request/response cycle. [Realtime SSE](realtime.md)
adds optional change notifications; fetching fresh content still uses HTTP.
Saving a database template also does not invent context or render every user's
screen: source checks happen on publication, final validation happens on render.

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

## 5. Follow a tap back to Django

Here is a complete, read-only example: tap **Ask Django for the time** and replace
only the greeting. Keep the configuration above, and replace
`hyperview/screens/home.xml` with:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<doc xmlns="https://hyperview.org/hyperview">
  <screen id="home">
    <body>
      <view id="greeting">
        <text>Hello from Django</text>
      </view>
      <text href="/hyperview/greeting/" verb="get"
            action="replace" target="greeting">Ask Django for the time</text>
    </body>
  </screen>
</doc>
```

Create `hyperview/fragments/greeting.xml`. A fragment is one bare element,
without a `doc`, `screen` or `body` wrapper:

```xml
<view xmlns="https://hyperview.org/hyperview" id="greeting">
  <text>Server time: {{ server_time|date:"H:i:s" }}</text>
</view>
```

Replace the URL example above with this URLconf (the small view is included here
so the example is self-contained):

```python
from django.urls import path
from django.utils import timezone

from dj_hyperview import HyperviewFragmentTemplateResponse, HyperviewTemplateView


def greeting(request):
    return HyperviewFragmentTemplateResponse(
        request,
        "fragments/greeting.xml",
        {"server_time": timezone.now()},
    )


urlpatterns = [
    path(
        "hyperview/home/",
        HyperviewTemplateView.as_view(template_name="screens/home.xml"),
        name="hyperview-home",
    ),
    path("hyperview/greeting/", greeting, name="hyperview-greeting"),
]
```

Open `/hyperview/home/` as your mobile client's entrypoint. The first response is
`application/vnd.hyperview+xml`. On a tap, Hyperview sends
`GET /hyperview/greeting/` to the same server. Django renders the time into a
validated `application/vnd.hyperview_fragment+xml` response. The client's
`action="replace"` replaces the element whose `id` is `greeting`; the rest of
this screen remains in place. No JSON API or custom JavaScript handler is needed
for this built-in behavior.

These demo endpoints are public and read-only. For real forms, keep permission
checks and data updates in Django, use POST for mutations and preserve
[CSRF protection](security.md). Do not use GET to save data.
See Hyperview's [behavior reference](https://hyperview.org/docs/reference_behavior_attributes)
for client actions and [HTTP responses](http-responses.md) for response details.

The screen remains owned by your project. Next, choose
[filesystem behavior](filesystem.md), [database publication](database-admin.md),
or the complete [configuration reference](configuration.md).
