"""Integration tests for the optional database template admin."""

import importlib
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.test import Client, override_settings
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
