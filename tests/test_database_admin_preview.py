"""Read-only, permission-bound transport for unsaved Admin previews."""

import json
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.contrib import admin
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(
        not apps.is_installed("django.contrib.admin"), reason="Admin profile required"
    ),
    pytest.mark.django_db,
]

ENABLED = {"ADMIN": {"EDITOR": True, "PREVIEW": {"ENABLED": True}}}
PREFIX = "admin:dj_hyperview_database_hyperviewtemplate_"
PAYLOAD = {"name": "draft.xml", "content": "<view />", "scenario": "empty"}
RESULT = {"ok": True, "hxml": "<view />", "diagnostics": []}


def _types():
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    return HyperviewTemplate, admin.site.get_model_admin(HyperviewTemplate)


def _url(obj=None):
    return (
        reverse(PREFIX + "hxml_preview_add")
        if obj is None
        else reverse(PREFIX + "hxml_preview_change", args=[obj.pk])
    )


@pytest.fixture
def renderer(monkeypatch):
    import dj_hyperview.contrib.database.admin as module

    callback = Mock(return_value=RESULT)
    monkeypatch.setattr(module, "render_preview", callback, raising=False)
    return callback


@override_settings(HYPERVIEW=ENABLED)
def test_preview_add_is_readonly_and_uncacheable(admin_client, renderer, monkeypatch):
    model, _ = _types()
    monkeypatch.setattr(model, "save", Mock(side_effect=AssertionError("save")))
    monkeypatch.setattr(
        model, "full_clean", Mock(side_effect=AssertionError("full_clean"))
    )
    response = admin_client.post(_url(), PAYLOAD, content_type="application/json")
    assert response.status_code == 200
    assert response.json() == RESULT
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]
    assert model.objects.count() == 0
    renderer.assert_called_once()
    assert renderer.call_args.args == ("draft.xml", "<view />", "empty")
    assert renderer.call_args.kwargs["request"].user.is_superuser


@override_settings(HYPERVIEW=ENABLED)
def test_preview_change_uses_object_permission_without_publication(
    admin_client, renderer, monkeypatch
):
    model, registered = _types()
    obj = model.objects.create(name="stored.xml", content="<view />")
    before = list(model.objects.values())
    permission = Mock(return_value=True)
    monkeypatch.setattr(registered, "has_change_permission", permission)
    response = admin_client.post(_url(obj), PAYLOAD, content_type="application/json")
    assert response.status_code == 200
    assert permission.call_args.args[1].pk == obj.pk
    assert list(model.objects.values()) == before
    permission.return_value = False
    assert (
        admin_client.post(
            _url(obj), PAYLOAD, content_type="application/json"
        ).status_code
        == 403
    )
    renderer.assert_called_once()
    assert (
        admin_client.post(
            reverse(PREFIX + "hxml_preview_change", args=[99999]),
            PAYLOAD,
            content_type="application/json",
        ).status_code
        == 404
    )


@pytest.mark.parametrize("feature", [{}, {"ADMIN": {"EDITOR": True}}])
def test_preview_disabled_returns_not_found(admin_client, renderer, feature):
    with override_settings(HYPERVIEW=feature):
        assert (
            admin_client.post(
                _url(), PAYLOAD, content_type="application/json"
            ).status_code
            == 404
        )
    renderer.assert_not_called()


@override_settings(HYPERVIEW=ENABLED)
def test_preview_requires_staff_mutation_permission(
    client, django_user_model, renderer
):
    from django.contrib.auth.models import Permission

    url = _url()
    assert client.post(url, PAYLOAD, content_type="application/json").status_code == 302
    user = django_user_model.objects.create_user(username="viewer", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_hyperviewtemplate"))
    client.force_login(user)
    assert client.post(url, PAYLOAD, content_type="application/json").status_code == 403
    renderer.assert_not_called()
    with override_settings(
        HYPERVIEW={
            "ADMIN": {
                "EDITOR": True,
                "PERMISSION": lambda request: True,
                "PREVIEW": {"ENABLED": True},
            }
        }
    ):
        assert (
            client.post(url, PAYLOAD, content_type="application/json").status_code
            == 200
        )
    with override_settings(
        HYPERVIEW={
            "ADMIN": {
                "EDITOR": True,
                "PERMISSION": lambda request: False,
                "PREVIEW": {"ENABLED": True},
            }
        }
    ):
        assert (
            client.post(url, PAYLOAD, content_type="application/json").status_code
            == 403
        )


@override_settings(HYPERVIEW=ENABLED)
def test_preview_csrf_and_methods(admin_user, admin_client, renderer):
    url = _url()
    for method in ("get", "put", "delete", "head"):
        response = getattr(admin_client, method)(url)
        assert response.status_code == 405
        assert response["Allow"] == "POST"
        assert "no-store" in response["Cache-Control"]
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin_user)
    assert (
        csrf_client.post(url, PAYLOAD, content_type="application/json").status_code
        == 403
    )
    csrf_client.get(reverse(PREFIX + "add"))
    token = csrf_client.cookies["csrftoken"].value
    assert (
        csrf_client.post(
            url, PAYLOAD, content_type="application/json", HTTP_X_CSRFTOKEN=token
        ).status_code
        == 200
    )
    renderer.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {**PAYLOAD, "context": {}},
        {**PAYLOAD, "name": "../secret"},
        {**PAYLOAD, "name": "x" * 256},
        {**PAYLOAD, "name": 1},
        {**PAYLOAD, "content": []},
        {**PAYLOAD, "content": "\ud800"},
        {**PAYLOAD, "scenario": "missing"},
        {**PAYLOAD, "scenario": None},
    ],
)
@override_settings(HYPERVIEW=ENABLED)
def test_preview_rejects_invalid_payload_before_render(admin_client, renderer, payload):
    response = admin_client.post(
        _url(), json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["hxml"] is None
    assert response.json()["diagnostics"][0]["coordinate_space"] is None
    renderer.assert_not_called()


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"{", "application/json"),
        (b"\xff", "application/json"),
        (b"{}", "text/plain"),
        (
            b'{"name":"one.xml","name":"two.xml","content":"<view/>","scenario":"empty"}',
            "application/json",
        ),
    ],
)
@override_settings(HYPERVIEW=ENABLED)
def test_preview_rejects_ambiguous_transport(
    admin_client, renderer, body, content_type
):
    response = admin_client.post(_url(), body, content_type=content_type)
    assert response.status_code == 400
    renderer.assert_not_called()


@override_settings(HYPERVIEW={**ENABLED, "VALIDATION": {"MAX_BYTES": 16}})
def test_preview_bounds_source_and_envelope_before_render(admin_client, renderer):
    for content in ("x" * 17, "é" * 9):
        assert (
            admin_client.post(
                _url(), {**PAYLOAD, "content": content}, content_type="application/json"
            ).status_code
            == 413
        )
    assert (
        admin_client.post(
            _url(), " " * 20000, content_type="application/json"
        ).status_code
        == 413
    )
    renderer.assert_not_called()


@override_settings(HYPERVIEW=ENABLED)
def test_preview_readonly_get_does_not_install_editor(client, django_user_model):
    from django.contrib.auth.models import Permission

    model, _ = _types()
    obj = model.objects.create(name="stored.xml", content="<view />")
    user = django_user_model.objects.create_user(username="reader", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_hyperviewtemplate"))
    client.force_login(user)
    response = client.get(reverse(PREFIX + "change", args=[obj.pk]))
    assert response.status_code == 200
    assert "djhv-preview-hxml" not in response.content.decode()
    assert "hxml_preview.js" not in response.content.decode()


@pytest.mark.parametrize(
    "route,locked",
    [
        ("change", True),
        ("delete", True),
        ("hxml_preview_change", False),
        ("hxml_preview_add", False),
    ],
)
def test_preview_queries_never_acquire_publication_locks(route, locked):
    _, registered = _types()
    request = RequestFactory().post("/")
    request.resolver_match = SimpleNamespace(url_name=PREFIX.split(":")[1] + route)
    assert registered.get_queryset(request).query.select_for_update is locked


@override_settings(
    HYPERVIEW={
        "ADMIN": {
            "EDITOR": True,
            "PREVIEW": {
                "ENABLED": True,
                "SCENARIOS": {
                    "sample": {
                        "LABEL": '<script>"&',
                        "CONTEXT": {"secret": "never-client"},
                    }
                },
            },
        }
    }
)
def test_preview_widget_encodes_only_scenario_labels_and_object_url():
    from dj_hyperview.contrib.database.admin import HyperviewTemplateAdminForm

    model, _ = _types()
    for obj in (None, model.objects.create(name="stored.xml", content="<view />")):
        form = HyperviewTemplateAdminForm(instance=obj)
        html = str(form["content"])
        assert 'data-hyperview-preview-url="' + _url(obj) in html
        assert 'class="button djhv-preview-hxml"' in html
        assert '<option value="sample">&lt;script&gt;&quot;&amp;</option>' in html
        assert "never-client" not in html
        assert "hxml_preview.js" in str(form.media)
        assert "hxml_preview_renderer.js" in str(form.media)
        assert "hxml_preview.css" in str(form.media)


@override_settings(HYPERVIEW=ENABLED)
def test_preview_widget_uses_its_owning_custom_admin_site(admin_user):
    from django.urls import path

    from dj_hyperview.contrib.database.admin import HyperviewTemplateAdmin

    model, _ = _types()
    site = admin.AdminSite(name="authoring")
    site.register(model, HyperviewTemplateAdmin)
    urls = ModuleType("preview_custom_urls")
    urls.urlpatterns = [path("custom/", site.urls), path("admin/", admin.site.urls)]
    request = RequestFactory().get("/custom/")
    request.user = admin_user
    with override_settings(ROOT_URLCONF=urls):
        form = site.get_model_admin(model).get_form(request)()
        assert 'data-hyperview-preview-url="/custom/' in str(form["content"])
        assert 'data-hyperview-preview-url="/admin/' not in str(form["content"])


def test_real_preview_reads_database_dependencies_without_mutation(
    admin_client, monkeypatch
):
    from django.core.cache import caches
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    from dj_hyperview.contrib.database import services

    model, _ = _types()
    obj = model.objects.create(name="stored.xml", content="<view />", active=False)
    model.objects.create(name="fragment.xml", content="<text>Database example</text>")
    before = list(model.objects.values())
    forbidden = Mock(side_effect=AssertionError("Preview attempted mutation"))
    monkeypatch.setattr(model, "save", forbidden)
    monkeypatch.setattr(model, "full_clean", forbidden)
    monkeypatch.setattr(transaction, "on_commit", forbidden)
    for name in ("publish_template", "rename_template", "delete_template"):
        monkeypatch.setattr(services, name, forbidden)
    for name in ("set", "add", "delete", "clear", "incr", "set_many", "delete_many"):
        monkeypatch.setattr(caches["default"], name, forbidden)
    config = {
        **ENABLED,
        "SOURCES": [
            {
                "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
                "OPTIONS": {"using": "default"},
            }
        ],
    }
    payload = {
        **PAYLOAD,
        "name": "renamed.xml",
        "content": (
            '<screen xmlns="https://hyperview.org/hyperview"><body>'
            '{% include "fragment.xml" %}</body></screen>'
        ),
    }
    with (
        override_settings(HYPERVIEW=config),
        CaptureQueriesContext(connection) as queries,
    ):
        response = admin_client.post(
            _url(obj), payload, content_type="application/json"
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True, response.json()
    assert "Database example" in response.json()["hxml"]
    assert queries.captured_queries
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)
    assert list(model.objects.values()) == before
    forbidden.assert_not_called()


@override_settings(HYPERVIEW=ENABLED)
def test_preview_safe_configuration_failure(admin_client, renderer):
    from dj_hyperview.exceptions import HyperviewConfigurationError

    renderer.side_effect = HyperviewConfigurationError("secret absolute path")
    response = admin_client.post(_url(), PAYLOAD, content_type="application/json")
    assert response.status_code == 500
    assert "secret" not in response.content.decode()


@override_settings(HYPERVIEW=ENABLED, DATA_UPLOAD_MAX_MEMORY_SIZE=8)
def test_preview_respects_django_request_limit(admin_client, renderer):
    assert (
        admin_client.post(_url(), PAYLOAD, content_type="application/json").status_code
        == 413
    )
    renderer.assert_not_called()


@override_settings(HYPERVIEW=ENABLED)
def test_preview_widget_retains_name_when_admin_name_field_is_readonly(admin_user):
    from dj_hyperview.contrib.database.admin import HyperviewTemplateAdmin

    model, _ = _types()
    obj = model.objects.create(name='draft"&.xml', content="<view />")
    registered = HyperviewTemplateAdmin(model, admin.site)
    registered.readonly_fields = (*registered.readonly_fields, "name")
    request = RequestFactory().get("/")
    request.user = admin_user
    form = registered.get_form(request, obj)(instance=obj)
    assert "name" not in form.fields
    assert 'data-hyperview-template-name="draft&quot;&amp;.xml"' in str(form["content"])


@override_settings(
    HYPERVIEW={**ENABLED, "VALIDATION": {"MAX_BYTES": 16}},
    DATA_UPLOAD_MAX_MEMORY_SIZE=None,
)
def test_preview_bounds_stream_when_content_length_is_absent():
    from io import BytesIO

    from dj_hyperview.contrib.database.admin_preview import _payload

    request = RequestFactory().post("/", "{}", content_type="application/json")
    request.META.pop("CONTENT_LENGTH", None)
    request._stream = BytesIO(b" " * 20000)
    assert _payload(request).status_code == 413


@override_settings(HYPERVIEW=ENABLED)
def test_preview_standalone_form_omits_controls_when_site_is_unregistered():
    from dj_hyperview.contrib.database.admin import HyperviewTemplateAdminForm

    urls = ModuleType("preview_no_admin_urls")
    urls.urlpatterns = []
    with override_settings(ROOT_URLCONF=urls):
        form = HyperviewTemplateAdminForm()
        assert "djhv-preview-hxml" not in str(form["content"])
        assert "hxml_preview.js" not in str(form.media)
