# Realtime with SSE: from a Django change to the screen

**Goal:** save a task in Django Admin and see its title update in an open list,
without navigating away and back. An open form should instead show a notice,
without automatically losing what the user is typing.

This guide targets **0.1.0a21**, which introduces the optional SSE primitives.
See the [a21 release notes](changelog.md#010a21-2026-09-09). Until its release
workflow completes, use the reviewed candidate checkout together with its
application integration. Installing a19 from the registry does not include SSE.
The a20 attempt was not published because its release checks failed.

!!! important "The package does not adapt your application automatically"
    `dj-hyperview` provides Python transport primitives. It does not automatically
    add a mobile listener, an authenticated endpoint or XML components.
    **HyperTodo already has that adapted client** and is this guide's example.
    For another application, also follow the own-application integration section.

## 1. Understand the flow

```text
Admin or a form saves a task
    → Django commits the transaction
    → Redis distributes “tasks may have changed”
    → the session's SSE connection receives that hint
    → the client chooses: reload the list or show Update
    → an authenticated GET fetches the current HXML document
    → the client confirms that response reached the screen layout
```

The hint **does not contain the task**, XML, its owner or a navigation URL.
It does not replace the POST response. Django remains the source of truth,
queried over HTTP with the same permissions and session as before.

Keep these three concepts separate:

| Concept | Purpose | Example |
| --- | --- | --- |
| Resource | Which information might be stale | `tasks` |
| Private topic | Who receives a Redis hint; selected by the server | User and database alias, never sent to the client |
| Screen declaration | Which resources a screen consumes and how to update it | Automatic list or form with a notice |

Terms used below: the **host** is the app containing the SSE reader and Hyperview
renderer; the **renderer** draws XML; **layout** places those elements on screen;
a **boundary** groups a screen and declares its update policy. **Reconciliation**
means fetching current data again, rather than assuming what changed.

**SSE is server → client.** Forms still use ordinary POST requests. There is no
WebSocket, durable replay or guarantee that every individual change is received.

## 2. What `[realtime]` installs and what you must provide

`[realtime]` is a **Python dependency extra**: it adds `redis>=7.4.1,<9`, the
client library for talking to a Redis server. It does not install:

- the Redis server;
- an ASGI server such as Uvicorn;
- Django Channels or a WebSocket server;
- mobile code or HyperTodo's `app:realtime` components.

The SSE APIs are part of the candidate code; the extra supplies their optional
dependency. Without Redis usage, the ordinary package still works and does not
import the Redis client at Django startup.

### Adopt the candidate

In **your application's Python environment**, not a global environment, install
the reviewed checkout. Replace this path with your actual candidate checkout:

```console
python -m pip install -e '/path/to/candidate/django-hv[realtime]'
```

This is an adoption command, not a new release. Verify the checkout and installed
package provenance. Do not change the pin to an invented version. If you choose
Uvicorn and it is not installed in that environment, install it **separately**:

```console
python -m pip install uvicorn
```

Django also needs a reachable Redis service. Use a development instance or your
deployment's credentials/ACLs, without reusing personal data for a test. In
production, configure TLS and minimum publish/subscribe permissions. See
[installation](installation.md), [configuration](configuration.md) and the
[official Django Uvicorn guide](https://docs.djangoproject.com/en/dev/howto/deployment/asgi/uvicorn/).

## 3. Happy path: try it in HyperTodo

File names and session policy in this section belong to **HyperTodo candidate**;
`HYPERVIEW["REALTIME"]` is the shared package setting. Use the checkout with both
backend and mobile integration. Paths below start at that checkout's root.

### Step 1: configure transport and sessions

Create `backend/config/settings_realtime_local.py` for your development test,
keeping HyperTodo's normal settings and adding this opt-in. Do not overwrite a
real deployment's credentials or database. The environment variables in this
example are not new package flags:

<!-- example: realtime-settings -->
```python
import os

from .settings import *  # Base HyperTodo settings, without replacing them.

SESSION_ENGINE = "django.contrib.sessions.backends.db"
HYPERVIEW = {
    **HYPERVIEW,  # Keep SOURCES, CACHE, XSD and the remaining base settings.
    "REALTIME": {
        "REDIS_URL": os.environ["APP_REDIS_URL"],
        "NAMESPACE": os.environ["APP_REALTIME_NAMESPACE"],
    },
}
```

Omitting `REALTIME` or using `None` disables it. When enabled, it requires exactly
`REDIS_URL` and `NAMESPACE`; HyperTodo additionally requires database sessions.
For example, `hypertodo-dev-alicia` uses lowercase letters, digits and hyphens;
underscores are also allowed, up to 64 characters. Choose a different namespace
for each application/environment.

If you previously used `HYPERTODO_REALTIME`, remove that key and move its two
values into `HYPERVIEW["REALTIME"]`. HyperTodo rejects the legacy key even when it
is `None`, avoiding two sources of configuration. An empty or invalid mapping
raises an error rather than silently disabling the service. There is no
`REALTIME.ALIAS` adapter. See the [complete HYPERVIEW example](configuration.md).

**Expected result:** Django accepts the settings without connecting to Redis on
import. The endpoint uses them when admitting an authorized session.

### Step 2: serve the correct ASGI entrypoint

HyperTodo already has this `backend/config/asgi.py` file. In another app, create
or adapt your entrypoint and replace `config.settings` with your settings module:

<!-- example: asgi -->
```python
import os

from django.core.asgi import get_asgi_application
from dj_hyperview.realtime import realtime_asgi

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = realtime_asgi(get_asgi_application())
```

The wrapper retains connection ownership for the **entire ASGI request**, even
with synchronous Django middleware. Do not replace it with a callback attached
only to the view's task.

Concrete **local development only** example, from `backend/`, with local Redis
and Uvicorn already available. In the same terminal, select the module you just
created. These values are credential-free examples; the guide does not start
Redis for you:

```console
export APP_REDIS_URL='redis://127.0.0.1:6379/0'
export APP_REALTIME_NAMESPACE='hypertodo-dev-alicia'
export DJANGO_SETTINGS_MODULE='config.settings_realtime_local'
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

The entrypoint's `setdefault` respects the explicit `DJANGO_SETTINGS_MODULE`.
`127.0.0.1` works for a client on the same machine, such as iOS Simulator; on a
phone it points to the phone, not your Mac. For a phone, replace `--host` with
your machine's verified local IP and use that address in Expo's API origin.
Keep the appropriate `ALLOWED_HOSTS` and trusted origins; do not open them
indiscriminately. An Android emulator needs its own host route or port forwarding;
do not assume its loopback is the host's loopback.

Do not bind a wildcard interface for convenience. The `make backend-run*`
commands using `runserver` serve ordinary HXML, **not this authenticated SSE endpoint**.

### Step 3: open the normal App and a list

1. Set Expo's API origin as described in HyperTodo's README: `extra.apiUrl`,
   including the `/hv/` prefix. Keep Host, Origin, cookies and CSRF valid;
   do not disable guards to obtain connectivity.
2. Start the normal mobile app following that README and open it in a compatible
   Expo Go version. Do not use a test screen that bypasses `DefaultApp`.
3. Sign in with a user from your test environment. Open **Tasks**, on its first
   page. You may select a filter, such as Active.
4. In Django Admin, edit the title of a visible task belonging to **that user**
   and save the ordinary Admin form.

**Expected result:** the focused page-one list performs an automatic document GET
and shows the new title, keeping the filter. Admin does not need to send
`notify-resources`: the model write triggers the producer. A different user must
not receive that private hint.

### Step 4: test a form without automatically losing its draft

Open New task and type without saving. Change related information in Admin.
A notice should appear, not an automatic reload or POST.

!!! warning "Update does not save the form"
    The notice retains the draft. **Pressing Update performs a document GET and
    may discard unsaved input.** This policy implements no merging, autosave
    or confirmation before discarding a draft.

After a local test, stop the processes you started. This guide assumes neither
running demo services nor shared credentials are left behind.

## 4. Add another list in HyperTodo

A new page using `tasks`, `categories` or `ui` **does not need another SSE
connection**. Each session has its connection; pages declare dependencies.

### Step 1: preserve the secure HTTP view

Add the route in `backend/todo/urls.py` and the view in `backend/todo/views.py`,
following `/hv/tasks/`. Authenticate, validate filters, scope the QuerySet to
the user and paginate. Reuse `_template_response`, which supplies
`realtime_context`; never force `realtime_enabled=True`.

Those helpers do not come with `dj-hyperview` for another app. Implement the
equivalent using your own authentication and context. XML does not grant access
to a QuerySet.

### Step 2: register the screen and canonical URL

In `backend/todo/realtime_templates.py`, define:

- The `_SCREENS` entry: template name → target and consumed resources.
- The canonical GET URL, with validated filters and no appended-page state.
- The screen mode. **Adding `_SCREENS` is not enough for a new list**: the current
  mode selection assigns `list` only to `tasks` and `categories`. Extend that
  selection as well if your XML uses `{{ realtime_mode }}`.

Tasks uses `target="task-list"`, resources `tasks categories ui` and
`refresh-href="/hv/tasks/?status=active&amp;fragment=list"`. The host removes
`fragment`, sets `page=1` and retains filters when reloading the document.
That URL must support a **complete document**, not only a fragment.

This extract shows how the three pieces fit together. It reduces the dictionary
to Tasks and Task form to illustrate the structure; **do not replace the full
dictionary** or remove other screens. `name`, `request` and `context` are already
available inside `realtime_context`; the view has validated its filters/category:

<!-- example: screen-mapping -->
```python
from urllib.parse import urlencode

_SCREENS = {
    "tasks": ("task-list", "tasks categories ui"),
    "task_form": ("task-form-screen", "tasks categories ui"),
}

# Inside realtime_context, after checking session negotiation:
target, resources = _SCREENS.get(name, ("", "ui"))
mode = "list" if name in {"tasks", "categories"} else "notice"
refresh_href = request.get_full_path()
if name == "tasks":
    filters = [("status", str(context["status_filter"]))]
    category = context.get("selected_category")
    if category is not None:
        filters.append(("category", str(category.pk)))
    filters.append(("fragment", "list"))
    refresh_href = "/hv/tasks/?" + urlencode(filters)
```

The helper returns `realtime_target`, `realtime_resources`, `realtime_mode` and
`realtime_refresh_href`. For a new list, change these **together**: its template
name/target in `_SCREENS`, the `mode="list"` selection and the branch building its
actual URL. Copying the registry entry alone is insufficient. Preserve the rest
of the helper: negotiation, correlated ID and fragment metadata.

For a form, the entry above uses the screen target and `notice`; the URL remains
the current GET, such as `/hv/tasks/new/` or an authorized edit. Do not use a POST
URL or remove the GET's ownership check.

### Step 3: wrap the screen and mark each actual page

This is a **reduced, validated teaching document**, not a replacement for Tasks
with all its styles, filters and actions. The `realtime_*` values come from the
authorized context above. In the real template, retain its
`{% if realtime_enabled %}` conditional and existing content.

<!-- example: list -->
```xml
<doc xmlns="https://hyperview.org/hyperview"
     xmlns:app="https://hypertodo.app/components">
  <screen id="tasks-screen">
    <body>
      <app:realtime resources="{{ realtime_resources }}"
          refresh-href="{{ realtime_refresh_href }}"
          target="task-list" mode="list">
        <list id="task-list">
          {% for task in tasks %}
          <item key="task-{{ task.id }}">
            {% if forloop.first %}
            <app:realtime-page request-id="{{ realtime_request_id }}"
                page="{{ page_obj.number }}" />
            {% endif %}
            <text>{{ task.title }}</text>
          </item>
          {% empty %}
          {% if page_obj.number == 1 %}
          <item key="empty">
            <app:realtime-page request-id="{{ realtime_request_id }}" page="1" />
            <text>No tasks.</text>
          </item>
          {% endif %}
          {% endfor %}
        </list>
      </app:realtime>
    </body>
  </screen>
</doc>
```

Placement rules:

1. **One boundary per screen**, inside `body`, after the screen's `styles` block
   if present, and outside `list`. Do not place it in the root navigator.
2. Put the marker inside the **first existing item** of each received page;
   do not add an extra item just for it. The empty example applies only to page 1.
3. An append response for page N carries N and the request-id of **that request**.
   Keep the existing `<items>`/`<list>` fragment; do not add a second boundary.
4. The context supplies `realtime_request_id` by reflecting a valid gate request
   ID. Do not use your own UUID, timestamp or fixed token to declare readiness.
   A marker without actual correlation cannot confirm layout.
5. Keep Django autoescape enabled: filter ampersands become `&amp;`, and user text
   does not become XML. Do not apply `safe` to these values.

### Step 4: verify policy, not just XML

| Screen state | On a relevant hint |
| --- | --- |
| Focused, ready list with loaded pages exactly `[1]` | Full-document GET; also updates filters, counters and styles |
| List with multiple loaded pages | Persistent notice; Update returns to page 1 |
| Unfocused screen in the current session | Retains the pending notice; applies list/notice policy when focused again |
| Paused app or unconfirmed session | Admits no private updates; resumes after session confirmation and reconciliation |
| Form or `notice` mode | Notice, without automatic reload |

A GET is not confirmation of rendering: the host waits for correlated layout.
Refresh does not promise scrolling to the top. Add filter, empty-list, append
and layout cases to the existing backend/mobile tests.

## 5. Add a form

The main difference is **`mode="notice"`**. Register the screen and a valid GET
URL in `backend/todo/realtime_templates.py`: `/hv/tasks/new/` or an authorized
object edit. Include categories in its dependencies if it shows a category picker.

This reduced document keeps one Django Form-bound field, errors and real CSRF.
It does not include all HyperTodo form fields or styles:

<!-- example: form -->
```xml
{% load dj_hyperview %}
<doc xmlns="https://hyperview.org/hyperview"
     xmlns:app="https://hypertodo.app/components">
  <screen id="task-form-screen">
    <body>
      <app:realtime resources="{{ realtime_resources }}"
          refresh-href="{{ realtime_refresh_href }}"
          target="task-form-screen" mode="notice">
        <app:realtime-page request-id="{{ realtime_request_id }}" page="1" />
        <view id="task-form-panel">
          <form id="task-form">
            <text-field name="title" value="{{ form.title.value|default:'' }}" />
            {% for error in form.title.errors %}<text>{{ error }}</text>{% endfor %}
            {% hv_csrf_token %}
            <view href="/hv/tasks/new/" verb="post" action="replace"
                target="task-form-panel"><text>Save</text></view>
          </form>
        </view>
      </app:realtime>
    </body>
  </screen>
</doc>
```

**Do not confuse the targets:** `task-form-screen` identifies document refresh;
`task-form-panel` identifies the submit response replacement. Render with request
in context so `{% hv_csrf_token %}` creates its hidden field.

The POST view must retain authorization, Form validation and CSRF. A 422 returns
the panel with values and errors; put the marker **as the first child of the
existing `<view id="task-form-panel">`**, without duplicating the boundary.
HyperTodo does this in `backend/hyperview/fragments/task_form_panel.xml`, guarded
by `realtime_fragment`. A resource update must not turn 422 into success, replay
the POST or treat a CSRF error as permission to clear the draft.

After saving, HyperTodo may notify **its own mobile coordinator** before going
back. This valid fragment shows only those behaviors:

<!-- example: local-notify -->
```xml
<view xmlns="https://hyperview.org/hyperview">
  <behavior trigger="load" action="notify-resources" resources="tasks" once="true" />
  <behavior trigger="load" action="back" />
</view>
```

`notify-resources` is local to that client. **It does not publish to Redis** or
notify other devices. The server-side after-commit producer does that.

## 6. Typed registration: what must be connected

These pieces are already connected in HyperTodo candidate. Do not duplicate them
or replace the application's entire registry when adding a page:

| Piece | HyperTodo file | Responsibility |
| --- | --- | --- |
| Screen context/policy | `backend/todo/realtime_templates.py` | Target, resources, canonical URL, mode and correlation |
| Component schema | `backend/schema/hypertodo.xsd` | `app:realtime` and `app:realtime-page` |
| Behavior descriptor | `backend/config/schema.py` | `notify-resources` and its required attribute |
| Validation settings | `backend/config/settings.py` | `HYPERVIEW.EXTRA_SCHEMAS` and `SCHEMA_EXTENSIONS` |
| Mobile components/policy | `mobile/src/realtime/gate.tsx`, `resources.tsx` | Boundary/marker and GET/layout coordination |
| Host registration | `mobile/src/realtime/app-session.tsx` | `AppSessionSurface` composes `gate.components` |
| Session connection | `mobile/src/realtime/event-stream.ts` | SSE through `expo/fetch`, lifecycle and reconnection |

The attributes `resources`, `refresh-href`, `target`, `mode`, `request-id` and
`page` **have no namespace prefix**. Only the custom elements use `app:`.
`SCHEMA_EXTENSIONS` registers typed behaviors/attributes; it installs no
JavaScript components. `EXTRA_SCHEMAS` registers custom XML elements.

This isolated `notify-resources` descriptor must be merged with your application's
other behaviors, not replace them:

<!-- example: registry -->
```python
REALTIME_SCHEMA_EXTENSIONS = {
    "BEHAVIORS": {
        "notify-resources": {
            "ATTRIBUTES": {
                "resources": {
                    "TYPE": "string",
                    "REQUIRED": True,
                    "ENUM": [
                        "tasks",
                        "categories",
                        "ui",
                        "tasks categories",
                        "tasks ui",
                        "categories ui",
                        "tasks categories ui",
                    ],
                },
            },
        },
    },
}
```

### Minimal schema for the two components

The following XSD validates the reduced examples. It is a **teaching subset**
of HyperTodo's contract, not a replacement for `hypertodo.xsd`, which also declares
other components. For your own host, save a local schema file and add its path
to `HYPERVIEW.EXTRA_SCHEMAS`; follow [custom schemas](custom-schemas.md).
Installing this XSD **without implementing the mobile components does not make
the screen work**.

<!-- example: schema -->
```xml
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:app="https://hypertodo.app/components"
    targetNamespace="https://hypertodo.app/components" elementFormDefault="qualified">
  <xs:simpleType name="realtime-resources">
    <xs:restriction base="xs:string">
      <xs:enumeration value="tasks" />
      <xs:enumeration value="categories" />
      <xs:enumeration value="ui" />
      <xs:enumeration value="tasks categories" />
      <xs:enumeration value="tasks ui" />
      <xs:enumeration value="categories ui" />
      <xs:enumeration value="tasks categories ui" />
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="realtime-request-id">
    <xs:restriction base="xs:string">
      <xs:minLength value="1" />
      <xs:maxLength value="80" />
      <xs:pattern value="[A-Za-z0-9_-]+" />
    </xs:restriction>
  </xs:simpleType>
  <xs:element name="realtime-page">
    <xs:complexType>
      <xs:attribute name="request-id" type="app:realtime-request-id" use="required" />
      <xs:attribute name="page" type="xs:positiveInteger" use="required" />
    </xs:complexType>
  </xs:element>
  <xs:element name="realtime">
    <xs:complexType>
      <xs:choice minOccurs="0" maxOccurs="unbounded">
        <xs:any namespace="https://hyperview.org/hyperview" processContents="strict" />
        <xs:element ref="app:realtime-page" />
      </xs:choice>
      <xs:attribute name="resources" type="app:realtime-resources" use="required" />
      <xs:attribute name="refresh-href" type="xs:anyURI" use="required" />
      <xs:attribute name="target" type="xs:string" use="required" />
      <xs:attribute name="mode" use="required">
        <xs:simpleType><xs:restriction base="xs:string">
          <xs:enumeration value="list" />
          <xs:enumeration value="notice" />
        </xs:restriction></xs:simpleType>
      </xs:attribute>
    </xs:complexType>
  </xs:element>
</xs:schema>
```

### A new page is not a new resource

The **package** accepts generic logical resources: 1–32 unique names per event,
each ASCII matching `[a-z][a-z0-9_-]{0,63}`. Another host could define `projects`
if it implements that contract end to end.

**HyperTodo** restricts its protocol to `tasks`, `categories`, `ui` and the seven
ordered combinations above. Dependencies are not inferred from route names:
Categories consumes tasks for its counts; Task form consumes categories for its picker.

Another screen using those resources only needs screen integration. Adding
`projects` requires coordinated changes to producers, the backend SSE validator,
client parser/state, behavior registry, XSD and tests. Writing
`resources="projects"` is insufficient: current clients reject it. Never put
owners, topics or tokens in `resources`.

## 7. Publish changes after commit

### In HyperTodo, reuse the existing producers first

`backend/todo/apps.py` connects receivers from `TodoConfig.ready()`.
`realtime_signals.py` observes Task/Category; `realtime_notifications.py` captures
private recipients and publishes after commit on the appropriate database alias.
Ordinary Django Admin and app saves therefore reach the owner's sessions, not
only the screen that sent the POST.

- `save()`/creation and object or QuerySet deletion have the described hooks.
- `QuerySet.update()`, `bulk_create()`, `bulk_update()` and direct SQL **do not
  trigger those Task/Category receivers**. For a bulk service, capture authorized
  owners and schedule an explicit notification once.
- When owners or relations change, consider both previous and new affected owners;
  do not calculate recipients from client-supplied parameters.
- Do not call the notifier again after an already-observed `save()`: that would
  duplicate the hint. `notify_after_commit` and `publish_after_commit` already
  schedule delivery; they do not routinely need another outer wrapper.

Database templates are different: the package exposes `TemplateInvalidation` and
`template_invalidated`. HyperTodo maps that event to `ui` for configured aliases.
The specialized template QuerySet covers its supported mutations; **this does
not change the rules for an ordinary Task model**. See the
[signal/transport API](api-reference.md) and [cache consistency](cache-consistency.md).

### Public helper for your own service

This helper is complete **as a publisher**, not as an endpoint or permission
validator. You can put it in `your_app/realtime_notifications.py`. It receives a
server-configured broker, a topic your policy already selected from the persisted
owner, and the write's database alias. It accepts no request:

<!-- example: publication -->
```python
from dj_hyperview.realtime import RedisBroker


def notify_task_change(broker: RedisBroker, *, owner_topic: str, using: str) -> None:
    broker.publish_after_commit(
        {"event": "invalidate", "data": {"version": 1, "resources": ["tasks"]}},
        (owner_topic,),
        using=using,
    )
```

Call it inside the service's same `transaction.atomic(using=using)`, after
validation and saving. The broker snapshots values before registering `on_commit`:
rollback publishes nothing; commit attempts publication. Without an open
transaction, Django executes `on_commit` immediately.

**Do not use `request.GET['owner']` or an XML topic.** The service must check
permission on the object, choose the database and derive its topic from server
data. HyperTodo's `private_topic(using, owner)` provides that encoding; it is not
a package API. A delivery failure does not roll back an already-committed save.

## 8. Integrate your own app: beyond the publisher

Do not copy an endpoint that only calls `subscribe(request.GET['topic'])`: that
would expose data across users. Implement your application layer using this
checklist, the [public APIs](api-reference.md) and [response contract](http-responses.md):

1. **Initialization:** register receivers from `AppConfig.ready()` without
   connecting Redis on import. Construct `RedisBroker(url, namespace)` from
   validated server settings. Keep the ASGI wrapper from section 3.
2. **Endpoint and authorization:** create an async view and route. Admit only
   the expected method/Accept/Origin/contract; verify the real session, active
   user, permissions and database. Reject client-chosen topics, owners or cursors.
   The SSE GET does not replace CSRF protection for writes.
3. **Bounded admission:** limit total connections and connections per identity.
   HyperTodo uses 256/4 per process, not a global distributed quota.
4. **Subscription:** derive topics on the server and await
   `subscription = await broker.subscribe(topics)`. This returns only after every
   ACK. Its first event is already `resync`; do not add another. Revalidate
   identity after this await **before sending headers or data**.
5. **Controller:** wrap the subscription to check current authorization before
   every frame/heartbeat, bound lifetime and validate your app's vocabulary.
   HyperTodo uses a 15 s heartbeat and 60 s lifetime. Heartbeat is `None` for
   `sse_response`, not evidence of a change.
6. **Ownership and cleanup:** within the view, transfer the controller to
   `sse_response(stream, aclose=stream.aclose)`. Its combined cleanup must close
   the subscription and release admission in `finally`, even if Redis fails.
   If response construction fails, the caller still owns cleanup. Do not rely
   only on a generator's `finally`: it may never be iterated.
7. **Client:** implement one connection per confirmed session, real cookies,
   a bounded SSE parser, pause/abort, reconnection and HTTP reconciliation.
   A result from an old identity must never update a new one. Do not pass a global
   logout callback to arbitrary resource hints.
8. **Host and screens:** register components/behaviors, canonical routes,
   screen dependencies and layout confirmation. Fetch completion or parsing
   alone does not confirm that a screen has updated.

**Complete reference, not a stub:** consult HyperTodo's
`backend/todo/realtime_views.py` (view), `realtime_auth.py` (authorization),
`realtime_stream.py` (controller/cleanup), `backend/config/urls.py`
(`GET /realtime/events/`, separate from screens under `/hv/`),
`backend/config/asgi.py` (wrapper) and `mobile/src/realtime/event-stream.ts`
(client). These files depend on HyperTodo's session contract; they are not a
universal view to copy without adaptation.

## 9. Cache, aliases and namespaces

Configuration is centralized in `HYPERVIEW["REALTIME"]`: REDIS_URL and NAMESPACE.
`get_settings().realtime` returns normalized immutable values for constructing
`RedisBroker(url, namespace)`. **`HYPERVIEW['REALTIME']['ALIAS']` is not currently
supported**: it is rejected, not interpreted as cache configuration.
Centralization does not require duplicated URLs: settings can take REDIS_URL
from a shared environment variable while preserving the transport contract.

Using a `CACHES` alias would require an explicit validated adapter. Django permits
memory, files, other backends and Redis configurations with multiple locations,
replicas and options. The generic cache API exposes neither PubSub nor async
subscription ownership. Do not extract clients/connections from a cache backend's
private attributes. See [Django caching](https://docs.djangoproject.com/en/6.0/topics/cache/).

Two cautions apply regardless of your settings structure:

- **Redis database numbers do not isolate PubSub.** A publisher on DB 10 can be
  heard by a subscriber on DB 1 on the same channel. Separate apps/environments
  with namespaces.
- `KEY_PREFIX`, TTL and cache-key invalidation do not control SSE channels.
  Namespaces prevent collisions; **they do not replace ACLs, authentication or permissions**.

[Redis Pub/Sub documents this behavior](https://redis.io/docs/latest/develop/pubsub/).
You may share the server if your deployment permits; sharing infrastructure is
not the same as sharing a cache contract.

## 10. Limits and practical diagnosis

| Observation | Check first |
| --- | --- |
| HXML works but there is no stream | Real ASGI server, active wrapper, opt-in and authorized session |
| Admin saves but another screen does not change | Correct owner, producer through `save()`, matching namespace and declared dependencies |
| Hint arrives without a reload | Form/notice, multiple pages, focus, pause or pending layout may correctly defer it |
| XSD rejects the page | Registered components, unqualified attributes, exact enum, placement and valid marker |
| Update reveals no change | Hints/resync are conservative; data may be unchanged |
| Reconnection or return from background | Confirm session and reconcile; never adopt another identity automatically or replay a POST |

Redis transport is **at-most-once**: hints can be lost. `resync` means
“reconcile”, not “a modification is confirmed”. There is no promised replay,
`Last-Event-ID`, durable queue, exactly-once delivery or offline synchronization.

Queues are bounded at 32; subscription overflow becomes resync. Setup/ACK has a
2 s budget. The publisher bounds caller wait to 2 s using one worker and a finite
queue per process, without retries. **This does not kill OS DNS resolution or
in-flight I/O:** a hint may finish late or partially. Socket timeout is per
operation, not a total worker deadline. The 2 s cleanup budget is cooperative;
it cannot guarantee cleanup of callbacks that ignore cancellation or a destroyed loop.

Do not log payloads, XML, cookies, bindings, private topics, passwords or
credential-bearing Redis URLs for diagnosis. Use bounded results, connection
state and authorized correlation without secrets. Do not disable limits or XSD
to make a test pass.

### Checklist before accepting a page integration

- [ ] GET/POST retain authentication, ownership, CSRF and 422 values/errors.
- [ ] A commit notifies once per producer; rollback does not; other accounts receive no private data.
- [ ] Page-one lists refresh the document while keeping filters; append retains a notice until Update.
- [ ] Forms keep drafts when notified; the user understands what Update does.
- [ ] Real layout correlation, pause/foreground and late responses cannot switch identity.
- [ ] Disconnect/error/cancellation release subscription and admission.
- [ ] Backend, host and chosen native environment have tests; simulated tests are not called native proof.

This guide's examples have syntax/import, real ASGI, commit/rollback, positive
and negative XSD, autoescape and CSRF tests. They start no Redis service and do
not alone prove mobile delivery. See [testing and its limits](testing.md).
