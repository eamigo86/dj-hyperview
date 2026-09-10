"""Execute the step-by-step guide's public examples, not a second implementation."""

import ast
import asyncio
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import pytest
from django import forms
from django.db import transaction
from django.template import Engine, RequestContext
from django.test import RequestFactory, override_settings
from django.urls import path

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.realtime import RedisBroker, sse_response

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "realtime.md"
HV = "https://hyperview.org/hyperview"
APP = "https://hypertodo.app/components"
urlpatterns = []


def example(name, language):
    source = GUIDE.read_text()
    pattern = rf"<!-- example: {name} -->\s*```{language}\n(.*?)\n```"
    match = re.search(pattern, source, re.DOTALL)
    assert match, f"Missing executable documentation example: {name}"
    return match[1]


def test_guide_python_examples_parse_and_relative_links_resolve():
    source = GUIDE.read_text()
    examples = re.findall(r"```python\n(.*?)\n```", source, re.DOTALL)
    assert examples
    for code in examples:
        ast.parse(code)
    links = re.findall(r"\]\(([^)]+)\)", source)
    assert links
    for link in links:
        if "://" not in link and not link.startswith("#"):
            assert (GUIDE.parent / link.split("#")[0]).is_file(), link


def test_documented_asgi_entry_streams_and_releases_through_real_django(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "tests.settings")

    async def run():
        released, sent = [], []

        async def events():
            yield {"event": "resync", "data": {"version": 1}}

        async def release():
            released.append(True)

        async def view(request):
            return sse_response(events(), aclose=release)

        global urlpatterns
        urlpatterns = [path("events", view)]
        with override_settings(
            ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"], MIDDLEWARE=[]
        ):
            namespace = {}
            exec(compile(example("asgi", "python"), str(GUIDE), "exec"), namespace)
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": b""})

            async def send(message):
                sent.append(message)

            await namespace["application"](
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/events",
                    "query_string": b"",
                    "headers": [(b"host", b"testserver")],
                },
                incoming.get,
                send,
            )
        assert sent[0]["status"] == 200
        assert b"".join(item.get("body", b"") for item in sent) == (
            b'event: resync\ndata: {"version":1}\n\n'
        )
        assert released == [True]

    asyncio.run(run())


@pytest.mark.parametrize("name", ["tasks", "task_form"])
def test_documented_screen_mapping_preserves_filters_or_form_get(name):
    request = RequestFactory().get(
        "/hv/tasks/new/"
        if name == "task_form"
        else "/hv/tasks/?status=active&page=2&fragment=items"
    )
    namespace = {
        "name": name,
        "request": request,
        "context": {
            "status_filter": "active",
            "selected_category": SimpleNamespace(pk="own-category"),
        },
    }
    exec(compile(example("screen-mapping", "python"), str(GUIDE), "exec"), namespace)
    assert namespace["resources"] == "tasks categories ui"
    if name == "tasks":
        assert namespace["mode"] == "list"
        assert namespace["target"] == "task-list"
        assert parse_qs(urlsplit(namespace["refresh_href"]).query) == {
            "status": ["active"],
            "category": ["own-category"],
            "fragment": ["list"],
        }
    else:
        assert namespace["mode"] == "notice"
        assert namespace["target"] == "task-form-screen"
        assert namespace["refresh_href"] == "/hv/tasks/new/"


@pytest.mark.django_db(transaction=True, databases=["default", "replica"])
def test_documented_publication_uses_selected_commit_and_never_rollback(monkeypatch):
    namespace, calls = {}, []
    exec(compile(example("publication", "python"), str(GUIDE), "exec"), namespace)
    module = importlib.import_module("dj_hyperview.realtime._broker")
    monkeypatch.setattr(module, "_dispatch", lambda *args: calls.append(args))
    broker = RedisBroker("redis://localhost:6379/0", "docs-test")
    notify = namespace["notify_task_change"]
    with transaction.atomic(using="default"):
        with transaction.atomic(using="replica"):
            notify(broker, owner_topic="private.default.owner", using="replica")
            assert calls == []
        assert len(calls) == 1
    assert calls[0][1] == ("docs-test:private.default.owner",)
    assert json.loads(calls[0][2]) == {
        "event": "invalidate",
        "data": {"version": 1, "resources": ["tasks"]},
    }
    with transaction.atomic(using="replica"):
        notify(broker, owner_topic="private.default.owner", using="replica")
        transaction.set_rollback(True, using="replica")
    assert len(calls) == 1


@pytest.fixture
def schema_settings(tmp_path):
    schema = tmp_path / "realtime.xsd"
    schema.write_text(example("schema", "xml"))
    namespace = {}
    exec(compile(example("registry", "python"), str(GUIDE), "exec"), namespace)
    with override_settings(
        HYPERVIEW={
            "EXTRA_SCHEMAS": [str(schema)],
            "SCHEMA_EXTENSIONS": namespace["REALTIME_SCHEMA_EXTENSIONS"],
        }
    ):
        yield


def render(name, **values):
    engine = Engine(
        libraries={"dj_hyperview": "dj_hyperview.templatetags.dj_hyperview"}
    )
    template = engine.from_string(example(name, "xml"))
    context = {
        "realtime_resources": "tasks categories ui",
        "realtime_request_id": "gate-doc-test-1",
        "realtime_refresh_href": "/hv/tasks/?status=active&fragment=list",
        "page_obj": {"number": 1},
        **values,
    }
    return template.render(RequestContext(RequestFactory().get("/hv/tasks/"), context))


@pytest.mark.parametrize("page,count", [(1, 0), (1, 2), (2, 1), (2, 0)])
def test_documented_list_has_one_correlated_marker_without_extra_items(
    schema_settings, page, count
):
    xml = render(
        "list",
        tasks=[{"id": n, "title": "A & <B>"} for n in range(count)],
        page_obj={"number": page},
    )
    validate_hyperview_schema(xml)
    root = ElementTree.fromstring(xml)
    markers = root.findall(f".//{{{APP}}}realtime-page")
    if count or page == 1:
        assert len(markers) == 1
        assert markers[0].attrib == {"request-id": "gate-doc-test-1", "page": str(page)}
    else:
        assert markers == []  # Empty append must not manufacture page-one readiness.
    assert len(root.findall(f".//{{{HV}}}item")) == (
        max(1, count) if page == 1 else count
    )
    assert root.find(f".//{{{APP}}}realtime").get("refresh-href") == (
        "/hv/tasks/?status=active&fragment=list"
    )
    if count:
        assert root.find(f".//{{{HV}}}text").text == "A & <B>"


def test_documented_form_preserves_bound_value_errors_and_real_csrf(schema_settings):
    class TaskForm(forms.Form):
        title = forms.CharField(max_length=3)

    form = TaskForm({"title": "A & <B>"})
    assert not form.is_valid()
    xml = render("form", form=form, realtime_refresh_href="/hv/tasks/new/")
    validate_hyperview_schema(xml)
    root = ElementTree.fromstring(xml)
    fields = {node.get("name"): node for node in root.findall(f".//{{{HV}}}text-field")}
    assert fields["title"].get("value") == "A & <B>"
    assert len(fields["csrfmiddlewaretoken"].get("value")) == 64
    assert any("3" in (node.text or "") for node in root.findall(f".//{{{HV}}}text"))
    boundary = root.find(f".//{{{APP}}}realtime")
    assert boundary.get("mode") == "notice"
    assert boundary.get("target") == "task-form-screen"
    assert (
        root.find(f'.//{{{HV}}}view[@verb="post"]').get("target") == "task-form-panel"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ('resources="tasks categories ui"', ""),
        ('resources="tasks categories ui"', 'resources="projects"'),
        ('mode="list"', 'mode="automatic"'),
        ('page="1"', 'page="0"'),
        ('request-id="gate-doc-test-1"', 'request-id="bad token"'),
    ],
)
def test_documented_schema_rejects_invalid_boundary_and_marker(
    schema_settings, old, new
):
    xml = render("list", tasks=[])
    assert old in xml
    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(xml.replace(old, new, 1))


def test_documented_local_behavior_has_typed_unqualified_resources(schema_settings):
    xml = example("local-notify", "xml")
    validate_hyperview_schema(xml)
    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(
            xml.replace('resources="tasks"', 'resources="projects"')
        )
    with pytest.raises(TemplateValidationError):
        validate_hyperview_schema(xml.replace(' resources="tasks"', ""))
