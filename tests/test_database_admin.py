"""Integration tests for the optional database template admin."""

import importlib
from importlib.resources import files
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from tests.test_database_app import run_isolated

pytestmark = pytest.mark.skipif(
    not apps.is_installed("django.contrib.admin"),
    reason="optional admin settings are not active",
)


def _admin_types() -> tuple[ModuleType, type[Any]]:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    return module, HyperviewTemplate


@pytest.mark.django_db
def test_default_site_registers_useful_standard_admin_once() -> None:
    module, model = _admin_types()

    assert admin.site.is_registered(model) is True
    registered = admin.site.get_model_admin(model)
    assert isinstance(registered, module.HyperviewTemplateAdmin)
    assert registered.form is module.HyperviewTemplateAdminForm
    assert registered.list_display == ("name", "active", "revision", "updated_at")
    assert registered.list_filter == ("active",)
    assert registered.search_fields == ("name",)
    assert registered.ordering == ("name",)
    assert registered.readonly_fields == ("revision", "created_at", "updated_at")

    admin.autodiscover()
    admin.autodiscover()
    assert admin.site.get_model_admin(model) is registered


@pytest.mark.django_db
def test_admin_form_saves_valid_template() -> None:
    module, model = _admin_types()
    form = module.HyperviewTemplateAdminForm(
        data={
            "name": "screens/home.xml",
            "content": "<view />",
            "active": True,
            "revision": 3,
        }
    )

    assert "revision" not in form.fields
    assert form.is_valid(), form.errors.as_data()
    template = form.save()
    assert model.objects.get() == template
    assert (template.name, template.content, template.revision) == (
        "screens/home.xml",
        "<view />",
        1,
    )


def test_admin_editor_is_opt_in_and_falls_back_to_textarea() -> None:
    """A basic database-admin installation retains Django's standard textarea."""
    module, _ = _admin_types()

    form = module.HyperviewTemplateAdminForm()

    assert type(form.fields["content"].widget).__name__ == "Textarea"


@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_enabled_admin_editor_uses_local_strict_csp_assets() -> None:
    """The optional widget exposes local assets and the protected catalog URL."""
    module, _ = _admin_types()

    form = module.HyperviewTemplateAdminForm()
    widget = form.fields["content"].widget
    rendered = str(widget.render("content", "<view />"))
    media = str(widget.media)

    assert type(widget).__name__ == "HyperviewAceWidget"
    assert 'data-mode="xml"' in rendered
    assert 'data-usestrictcsp="true"' in rendered
    assert 'data-hyperview-catalog-url="/admin/' in rendered
    assert "Format and Validate" in rendered
    assert "https://" not in media
    assert "dj_hyperview/admin/hxml_mode.js" in media
    assert "dj_hyperview/admin/hxml_editor.js" in media


def test_admin_editor_stretches_across_django_admin_flex_layout() -> None:
    """The editor must not collapse to its gutter inside Django admin forms."""
    stylesheet = (
        files("dj_hyperview")
        .joinpath("static/dj_hyperview/admin/hxml_editor.css")
        .read_text(encoding="utf-8")
    )

    assert "align-self: stretch" in stylesheet
    assert "min-width: 0" in stylesheet
    assert "width: 100%" in stylesheet


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_schema_catalog_admin_endpoint_requires_authentication_and_permission(
    client, admin_client, django_user_model
) -> None:
    """Completion metadata is available only through the protected model admin."""
    url = reverse("admin:dj_hyperview_database_hyperviewtemplate_hxml_catalog")

    anonymous = client.get(url)
    staff = django_user_model.objects.create_user(
        username="catalog-reader", password="secret", is_staff=True
    )
    client.force_login(staff)
    forbidden = client.get(url)
    allowed = admin_client.get(url)

    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].startswith("/admin/login/?next=")
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["schema_version"] == "0.110.0"
    assert "view" in allowed.json()["elements"]


def test_database_contrib_without_admin_never_imports_admin_module() -> None:
    result = run_isolated(
        "tests.settings_database",
        "import django,sys; django.setup(); "
        "print('django.contrib.admin' in sys.modules, "
        "'dj_hyperview.contrib.database.admin' in sys.modules)",
    )

    assert (result.returncode, result.stdout.strip()) == (0, "False False")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "content", "field", "code"),
    [
        ("../private.xml", "<view />", "name", "invalid"),
        ("bad\n.xml", "<view />", "name", "invalid"),
        ("bad\ud800.xml", "<view />", "name", "invalid"),
        (
            "screen.xml",
            "<!DOCTYPE view SYSTEM 'https://invalid.test/x'><view />",
            "content",
            "forbidden_declaration",
        ),
        (
            "screen.xml",
            "{% if enabled %}<view />",
            "content",
            "django_syntax",
        ),
    ],
)
def test_admin_form_reports_existing_field_validation(
    name: str, content: str, field: str, code: str
) -> None:
    module, model = _admin_types()
    form = module.HyperviewTemplateAdminForm(
        data={"name": name, "content": content, "active": True, "revision": 1}
    )

    assert form.is_valid() is False
    assert code in {error.code for error in form.errors.as_data()[field]}
    assert model.objects.count() == 0
    assert name not in str(form.errors.as_data())
    assert content not in str(form.errors.as_data())


@pytest.mark.django_db
@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10}})
def test_admin_form_uses_configured_source_limit() -> None:
    module, model = _admin_types()
    form = module.HyperviewTemplateAdminForm(
        data={
            "name": "screen.xml",
            "content": "<view>secret</view>",
            "active": True,
            "revision": 1,
        }
    )

    assert form.is_valid() is False
    assert form.errors.as_data()["content"][0].code == "max_bytes"
    assert "secret" not in str(form.errors.as_data())
    assert model.objects.count() == 0


@pytest.mark.django_db
def test_admin_form_reports_duplicate_name_on_name_field() -> None:
    module, model = _admin_types()
    model.objects.create(name="screen.xml", content="<view />")
    form = module.HyperviewTemplateAdminForm(
        data={
            "name": "screen.xml",
            "content": "<other />",
            "active": True,
            "revision": 1,
        }
    )

    assert form.is_valid() is False
    assert {error.code for error in form.errors.as_data()["name"]} == {"unique"}
    assert model.objects.count() == 1


def test_delete_list_filter_truncates_and_escapes_nested_items() -> None:
    """The portable admin filter limits output without trusting object labels."""
    module = importlib.import_module(
        "dj_hyperview.contrib.database.templatetags.dj_hyperview_admin"
    )

    rendered = str(module.truncated_unordered_list(["<first>", "second", "third"], 2))

    assert "&lt;first&gt;" in rendered
    assert "second" in rendered
    assert "third" not in rendered
    assert "1 more object" in rendered


@pytest.mark.django_db
def test_admin_client_requires_authentication(client) -> None:
    _, model = _admin_types()
    url = reverse("admin:dj_hyperview_database_hyperviewtemplate_changelist")

    response = client.get(url)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/?next=")
    assert model.objects.count() == 0


@pytest.mark.django_db
def test_admin_mutation_requires_csrf(admin_user) -> None:
    _, model = _admin_types()
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")

    response = client.post(
        add,
        {
            "name": "screen.xml",
            "content": "<view />",
            "active": "on",
            "_save": "Save",
        },
    )

    assert response.status_code == 403
    assert model.objects.count() == 0


@pytest.mark.django_db
def test_admin_mutations_default_to_superusers_even_with_model_permissions(
    django_user_model,
) -> None:
    """Django model permissions cannot bypass the secure package default."""
    from django.contrib.auth.models import Permission

    module, model = _admin_types()
    editor = django_user_model.objects.create_user(
        username="template-editor", password="secret", is_staff=True
    )
    editor.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label=model._meta.app_label,
            content_type__model=model._meta.model_name,
        )
    )
    request = RequestFactory().get("/admin/")
    request.user = editor
    registered = admin.site.get_model_admin(model)

    assert isinstance(registered, module.HyperviewTemplateAdmin)
    assert registered.has_add_permission(request) is False
    assert registered.has_change_permission(request) is False
    assert registered.has_delete_permission(request) is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "permission",
    [
        lambda request: bool(request.user.is_staff),
        "tests.stubs.allow_template_admin",
    ],
)
def test_admin_permission_callback_authorizes_all_template_mutations(
    django_user_model, permission
) -> None:
    """Callable and dotted policies become the mutation permission boundary."""
    _, model = _admin_types()
    editor = django_user_model.objects.create_user(
        username="template-editor", password="secret", is_staff=True
    )
    request = RequestFactory().get("/admin/")
    request.user = editor
    registered = admin.site.get_model_admin(model)

    with override_settings(HYPERVIEW={"ADMIN": {"PERMISSION": permission}}):
        assert registered.has_add_permission(request) is True
        assert registered.has_change_permission(request) is True
        assert registered.has_delete_permission(request) is True


@pytest.mark.parametrize(
    "permission",
    [lambda request: "yes", lambda request: 1 / 0],
)
def test_admin_permission_callback_fails_closed(permission) -> None:
    """Callback errors and non-boolean results never grant mutation access."""
    _, model = _admin_types()
    request = RequestFactory().get("/admin/")
    request.user = type("User", (), {"is_superuser": False})()
    registered = admin.site.get_model_admin(model)

    with override_settings(HYPERVIEW={"ADMIN": {"PERMISSION": permission}}):
        assert registered.has_add_permission(request) is False
        assert registered.has_change_permission(request) is False
        assert registered.has_delete_permission(request) is False


@pytest.mark.django_db
def test_superuser_can_complete_admin_crud_through_publication_services(
    admin_client,
) -> None:
    _, model = _admin_types()
    changelist = reverse("admin:dj_hyperview_database_hyperviewtemplate_changelist")
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")

    assert admin_client.get(changelist).status_code == 200
    assert admin_client.get(add).status_code == 200
    added = admin_client.post(
        add,
        {
            "name": "screens/home.xml",
            "content": "<view />",
            "active": "on",
            "revision": 99,
            "_save": "Save",
        },
    )
    assert (added.status_code, added.headers["Location"]) == (302, changelist)
    template = model.objects.get()
    assert template.revision == 1

    change = reverse(
        "admin:dj_hyperview_database_hyperviewtemplate_change",
        args=[template.pk],
    )
    assert admin_client.get(change).status_code == 200
    changed = admin_client.post(
        change,
        {
            "name": "screens/updated.xml",
            "content": "<view><text>updated</text></view>",
            "active": "on",
            "expected_revision": "1",
            "revision": 99,
            "_save": "Save",
        },
    )
    assert (changed.status_code, changed.headers["Location"]) == (302, changelist)
    template.refresh_from_db()
    assert (template.name, template.content, template.revision) == (
        "screens/updated.xml",
        "<view><text>updated</text></view>",
        2,
    )

    delete = reverse(
        "admin:dj_hyperview_database_hyperviewtemplate_delete",
        args=[template.pk],
    )
    assert admin_client.get(delete).status_code == 200
    deleted = admin_client.post(delete, {"post": "yes", "expected_revision": "2"})
    assert (deleted.status_code, deleted.headers["Location"]) == (302, changelist)
    assert model.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "content"), [("../unsafe.xml", "<view />")])
def test_admin_add_rejects_invalid_fields_without_writing(
    admin_client, name: str, content: str
) -> None:
    _, model = _admin_types()
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")

    response = admin_client.post(
        add,
        {"name": name, "content": content, "active": "on", "_save": "Save"},
    )

    assert response.status_code == 200
    assert set(response.context["adminform"].form.errors.as_data()) == {
        "name" if name.startswith("..") else "content"
    }
    assert model.objects.count() == 0


@pytest.mark.django_db
def test_changelist_escapes_hostile_name(admin_client) -> None:
    _, model = _admin_types()
    model.objects.create(name="screens/<script>.xml", content="<view />")
    url = reverse("admin:dj_hyperview_database_hyperviewtemplate_changelist")

    rendered = admin_client.get(url).content.decode()

    assert "screens/&lt;script&gt;.xml" in rendered
    assert "screens/<script>.xml" not in rendered


@pytest.mark.django_db
@pytest.mark.parametrize("editor", [False, True])
def test_view_only_staff_can_open_template_without_edit_controls(
    client, django_user_model, editor: bool
) -> None:
    """Read-only forms omit the content field even when the Ace editor is enabled."""
    from django.contrib.auth.models import Permission

    _, model = _admin_types()
    template = model.objects.create(name="screen.xml", content="<view />")
    reader = django_user_model.objects.create_user(
        username="template-reader", password="secret", is_staff=True
    )
    reader.user_permissions.add(
        Permission.objects.get(
            content_type__app_label=model._meta.app_label,
            codename=f"view_{model._meta.model_name}",
        )
    )
    client.force_login(reader)
    url = reverse(
        "admin:dj_hyperview_database_hyperviewtemplate_change", args=[template.pk]
    )

    with override_settings(HYPERVIEW={"ADMIN": {"EDITOR": editor}}):
        response = client.get(url)
        rejected = client.post(url, {"name": "changed.xml", "content": "<changed />"})

    assert response.status_code == 200
    assert "content" not in response.context["adminform"].form.fields
    assert response.context["has_change_permission"] is False
    assert response.context["has_delete_permission"] is False
    assert b'name="_save"' not in response.content
    assert b'class="deletelink"' not in response.content
    assert b"djhv-format-validate" not in response.content
    assert b"&lt;view /&gt;" in response.content
    assert rejected.status_code == 403
    template.refresh_from_db()
    assert (template.name, template.content, template.revision) == (
        "screen.xml",
        "<view />",
        1,
    )
