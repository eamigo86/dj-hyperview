"""Integration tests for the optional database template admin."""

import importlib
import logging
from importlib.resources import files
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.db import connection
from django.test import Client, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from dj_hyperview import HYPERVIEW_SCHEMA_VERSION
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
    assert 'data-theme="monokai"' in rendered
    assert "django_ace/ace/theme-monokai.js" in media
    assert "django_ace/ace/css/theme/monokai.css" in media
    assert "textmate" not in media
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
    assert allowed.json()["schema_version"] == HYPERVIEW_SCHEMA_VERSION
    assert "view" in allowed.json()["elements"]


@pytest.mark.django_db
@pytest.mark.parametrize("hyperview", [{}, {"ADMIN": {"EDITOR": False}}])
def test_schema_catalog_admin_endpoint_is_not_found_when_the_editor_is_disabled(
    admin_client, hyperview
) -> None:
    """The validation endpoint hides itself without the editor; so must the catalog."""
    url = reverse("admin:dj_hyperview_database_hyperviewtemplate_hxml_catalog")

    with override_settings(HYPERVIEW=hyperview):
        response = admin_client.get(url)

    assert response.status_code == 404


def _conflicting_extra_schemas(tmp_path: Any) -> list[Any]:
    """Write two schemas that compile alone and declare one name differently."""
    paths = []
    for index, declared in enumerate(("xs:string", "xs:integer")):
        path = tmp_path / f"conflict{index}.xsd"
        path.write_text(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="urn:app" elementFormDefault="qualified">'
            f'<xs:element name="w" type="{declared}"/></xs:schema>',
            encoding="utf-8",
        )
        paths.append(path)
    return paths


@pytest.mark.django_db
def test_admin_degrades_when_the_extra_schemas_conflict(admin_client, tmp_path) -> None:
    """An unusable configuration denies edits but never crashes the Admin.

    The change page of an existing template renders read-only without the editor,
    adding is denied like any other mutation, and the editor endpoints answer with
    the same JSON error as the validation endpoint instead of a server error.
    """
    _, model = _admin_types()
    template = model.objects.create(name="screens/home.xml", content="<view />")
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"
    configured = {
        "ADMIN": {"EDITOR": True},
        "EXTRA_SCHEMAS": _conflicting_extra_schemas(tmp_path),
    }

    with override_settings(HYPERVIEW=configured):
        change = admin_client.get(reverse(prefix + "change", args=[template.pk]))
        add = admin_client.get(reverse(prefix + "add"))
        catalog = admin_client.get(reverse(prefix + "hxml_catalog"))
        validate = admin_client.post(
            reverse(prefix + "hxml_validate"),
            data={"name": "a.xml", "content": "<view />"},
            content_type="application/json",
        )

    assert change.status_code == 200
    assert "data-hyperview-editor" not in change.content.decode()
    assert add.status_code == 403
    for response in (catalog, validate):
        assert response.status_code == 500
        assert response.json()["diagnostics"][0]["code"] == "configuration_error"
    assert str(tmp_path) not in catalog.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["changelist", "add", "change"])
def test_unusable_settings_are_one_warning_without_a_traceback(
    admin_client, tmp_path, caplog, page
) -> None:
    """A configuration fault is not a failing callback; the callback never ran.

    Each page used to log an ERROR with a traceback saying that ADMIN.PERMISSION
    failed, which sent operators to a callback that was never called.
    """
    module, model = _admin_types()
    template = model.objects.create(name="screens/home.xml", content="<view />")
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"
    arguments = [template.pk] if page == "change" else []
    configured = {
        "ADMIN": {"EDITOR": True},
        "EXTRA_SCHEMAS": _conflicting_extra_schemas(tmp_path),
    }

    with override_settings(HYPERVIEW=configured):
        with caplog.at_level(logging.DEBUG):
            admin_client.get(reverse(prefix + page, args=arguments))

    records = [
        record
        for record in caplog.records
        if record.name == module.__name__ and record.levelno >= logging.WARNING
    ]
    assert [record.levelno for record in records] == [logging.WARNING]
    assert records[0].exc_info is None
    assert "ADMIN.PERMISSION failed" not in records[0].getMessage()
    assert "HYPERVIEW" in records[0].getMessage()
    assert str(tmp_path) not in caplog.text


@pytest.mark.django_db
def test_a_failing_permission_callback_is_still_an_error_with_its_traceback(
    admin_client, caplog
) -> None:
    module, _ = _admin_types()
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"

    with override_settings(HYPERVIEW={"ADMIN": {"PERMISSION": lambda request: 1 / 0}}):
        with caplog.at_level(logging.DEBUG):
            response = admin_client.get(reverse(prefix + "add"))

    records = [r for r in caplog.records if r.name == module.__name__]
    assert response.status_code == 403
    assert [r.levelno for r in records] == [logging.ERROR]
    assert "ADMIN.PERMISSION failed" in records[0].getMessage()
    assert records[0].exc_info is not None


@pytest.mark.django_db
def test_admin_writes_are_denied_and_change_nothing_when_the_schemas_conflict(
    admin_client, tmp_path
) -> None:
    """Saving or deleting is refused before any form runs, not just hidden."""
    _, model = _admin_types()
    template = model.objects.create(name="screens/home.xml", content="<view />")
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"
    configured = {"EXTRA_SCHEMAS": _conflicting_extra_schemas(tmp_path)}

    with override_settings(HYPERVIEW=configured):
        change = admin_client.post(
            reverse(prefix + "change", args=[template.pk]),
            data={"name": "screens/home.xml", "content": "<view>changed</view>"},
        )
        delete = admin_client.post(
            reverse(prefix + "delete", args=[template.pk]), data={"post": "yes"}
        )
        add = admin_client.post(
            reverse(prefix + "add"),
            data={"name": "screens/new.xml", "content": "<view />", "active": "on"},
        )

    assert (change.status_code, delete.status_code, add.status_code) == (403, 403, 403)
    template.refresh_from_db()
    assert template.content == "<view />"
    assert template.revision == 1
    assert model.objects.count() == 1


@pytest.mark.django_db
@override_settings(HYPERVIEW={"ADMIN": {"EDITOR": True}})
def test_catalog_endpoint_hides_the_cause_when_a_schema_changes_after_startup(
    admin_client, monkeypatch
) -> None:
    from dj_hyperview.exceptions import TemplateValidationError

    module, _ = _admin_types()

    def broken() -> None:
        raise TemplateValidationError("schema_invalid", "private schema path")

    monkeypatch.setattr(module, "get_hyperview_catalog", broken)
    url = reverse("admin:dj_hyperview_database_hyperviewtemplate_hxml_catalog")

    response = admin_client.get(url)

    assert response.status_code == 500
    assert response.json()["diagnostics"][0]["code"] == "configuration_error"
    assert "private schema path" not in response.content.decode()


@pytest.mark.django_db
def test_admin_form_uses_a_textarea_and_fails_closed_when_settings_are_unusable(
    tmp_path,
) -> None:
    """The form builds and validates without the settings; it never validates a draft.

    A blank content keeps the model field validator, which does read the settings
    and fails loudly for a stored template, out of this test.
    """
    module, _ = _admin_types()
    configured = {
        "ADMIN": {"EDITOR": True},
        "EXTRA_SCHEMAS": _conflicting_extra_schemas(tmp_path),
    }

    with override_settings(HYPERVIEW=configured):
        form = module.HyperviewTemplateAdminForm(
            data={"name": "screen.xml", "content": "", "active": True}
        )
        valid = form.is_valid()

    assert type(form.fields["content"].widget).__name__ == "Textarea"
    assert valid is False
    assert form.non_field_errors().as_data()[0].code == "configuration_error"


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
        ("bad\N{RIGHT-TO-LEFT OVERRIDE}.xml", "<view />", "name", "invalid"),
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


def _truncated(items: list[Any], limit: int | None) -> str:
    module = importlib.import_module(
        "dj_hyperview.contrib.database.templatetags.dj_hyperview_admin"
    )
    return str(module.truncated_unordered_list(items, limit))


def test_delete_list_filter_keeps_a_parent_whose_children_cross_the_limit() -> None:
    """Visibility is decided before the children are counted, as Django does."""
    rendered = _truncated(["a", ["b"]], 1)

    assert (
        rendered == "\t<li>a</li>\n\t<li>\N{HORIZONTAL ELLIPSIS}and 1 more object.</li>"
    )
    assert "<ul>" not in rendered


def test_delete_list_filter_counts_hidden_descendants_in_the_remainder() -> None:
    rendered = _truncated(["a", ["b", "c"], "d"], 2)

    assert rendered == (
        "\t<li>a\n\t<ul>\n\t\t<li>b</li>\n\t</ul>\n\t</li>"
        "\n\t<li>\N{HORIZONTAL ELLIPSIS}and 2 more objects.</li>"
    )


def test_delete_list_filter_renders_nested_items_without_a_limit() -> None:
    assert _truncated(["a", ["b"]], None) == (
        "\t<li>a\n\t<ul>\n\t\t<li>b</li>\n\t</ul>\n\t</li>"
    )


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


@pytest.mark.django_db
@pytest.mark.parametrize("content", ["\n<view />\n", "  <view />  ", "<view />\n\n"])
def test_admin_form_keeps_content_byte_exact(content: str) -> None:
    """The form does not strip stored sources, as Django's CharField would."""
    module, _ = _admin_types()
    form = module.HyperviewTemplateAdminForm(
        data={"name": "screen.xml", "content": content, "active": True}
    )

    assert form.is_valid(), form.errors.as_data()
    assert form.cleaned_data["content"] == content


@pytest.mark.django_db
def test_admin_save_round_trips_surrounding_whitespace(admin_client) -> None:
    _, model = _admin_types()
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")
    source = "<view>\n  <text>a</text>\n</view>\n"

    admin_client.post(
        add, {"name": "screen.xml", "content": source, "active": "on", "_save": "Save"}
    )
    template = model.objects.get()
    change = reverse(
        "admin:dj_hyperview_database_hyperviewtemplate_change", args=[template.pk]
    )
    admin_client.post(
        change,
        {
            "name": "screen.xml",
            "content": source + "\n",
            "active": "on",
            "expected_revision": "1",
            "_save": "Save",
        },
    )

    template.refresh_from_db()
    assert (template.content, template.revision) == (source + "\n", 2)


def _counting_permission(calls: list[object], result: object = True) -> Any:
    def permission(request: Any) -> object:
        calls.append(request)
        if isinstance(result, Exception):
            raise result
        return result

    return permission


@pytest.mark.parametrize("result", [True, False, ZeroDivisionError()])
def test_admin_permission_is_evaluated_once_per_request(result: object) -> None:
    """Every permission check of one request shares a single callback result."""
    _, model = _admin_types()
    registered = admin.site.get_model_admin(model)
    calls: list[object] = []
    first = RequestFactory().get("/admin/")
    second = RequestFactory().get("/admin/")

    with override_settings(
        HYPERVIEW={"ADMIN": {"PERMISSION": _counting_permission(calls, result)}}
    ):
        outcomes = {
            check(first)
            for check in (
                registered.has_add_permission,
                registered.has_change_permission,
                registered.has_delete_permission,
                registered.has_add_permission,
            )
        }
        assert len(calls) == 1
        registered.has_change_permission(second)

    assert outcomes == {result is True}
    assert calls == [first, second]


@pytest.mark.django_db
def test_admin_pages_evaluate_the_permission_callback_once(admin_client) -> None:
    _, model = _admin_types()
    template = model.objects.create(name="screen.xml", content="<view />")
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"
    calls: list[object] = []

    with override_settings(
        HYPERVIEW={"ADMIN": {"PERMISSION": _counting_permission(calls)}}
    ):
        for url in (
            reverse(prefix + "changelist"),
            reverse(prefix + "add"),
            reverse(prefix + "change", args=[template.pk]),
        ):
            calls.clear()
            assert admin_client.get(url).status_code == 200
            assert len(calls) == 1, url


@pytest.mark.django_db
def test_changelist_defers_the_template_bodies(admin_client) -> None:
    _, model = _admin_types()
    template = model.objects.create(name="screen.xml", content="<view>body</view>")
    prefix = "admin:dj_hyperview_database_hyperviewtemplate_"

    with CaptureQueriesContext(connection) as listing:
        listed = admin_client.get(reverse(prefix + "changelist"))
    with CaptureQueriesContext(connection) as editing:
        edited = admin_client.get(reverse(prefix + "change", args=[template.pk]))

    def selects_content(queries: Any) -> bool:
        return any(
            "hyperviewtemplate" in query["sql"] and '"content"' in query["sql"]
            for query in queries.captured_queries
        )

    assert listed.status_code == 200 and b"screen.xml" in listed.content
    assert not selects_content(listing)
    assert edited.status_code == 200 and b"&lt;view&gt;body" in edited.content
    assert selects_content(editing)


@pytest.mark.django_db
def test_bulk_delete_action_works_on_the_deferred_changelist(admin_client) -> None:
    _, model = _admin_types()
    template = model.objects.create(name="screen.xml", content="<view />")
    changelist = reverse("admin:dj_hyperview_database_hyperviewtemplate_changelist")

    response = admin_client.post(
        changelist,
        {
            "action": "delete_selected",
            "_selected_action": [str(template.pk)],
            "post": "yes",
        },
    )

    assert response.status_code == 302
    assert model.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="tests.urls_admin_custom", HYPERVIEW={"ADMIN": {"EDITOR": True}}
)
def test_editor_urls_follow_the_admin_site_the_model_is_registered_on(
    admin_client,
) -> None:
    """A non-default AdminSite gets its own validate and catalog endpoints."""
    prefix = "dj_hyperview_database_hyperviewtemplate_"
    catalog = reverse("custom:" + prefix + "hxml_catalog")
    validate = reverse("custom:" + prefix + "hxml_validate")

    custom = admin_client.get(reverse("custom:" + prefix + "add")).content.decode()
    default = admin_client.get(reverse("admin:" + prefix + "add")).content.decode()

    assert catalog.startswith("/custom/")
    assert f'data-hyperview-catalog-url="{catalog}"' in custom
    assert f'data-hyperview-validation-url="{validate}"' in custom
    assert (
        f'data-hyperview-catalog-url="{reverse("admin:" + prefix + "hxml_catalog")}"'
        in default
    )
    assert admin_client.get(catalog).status_code == 200
    posted = admin_client.post(
        validate,
        data={"name": "a.xml", "content": "<view />"},
        content_type="application/json",
    )
    assert posted.status_code == 200
