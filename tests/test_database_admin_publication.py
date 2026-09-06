"""Conflict-safe database admin publication integration tests."""

import importlib
import json
from typing import Any
from unittest.mock import call, patch

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.test import override_settings
from django.urls import reverse
from django.utils import translation

from dj_hyperview.contrib.database._identity import template_name_identity
from dj_hyperview.contrib.database.services import PublicationConflict
from dj_hyperview.exceptions import SourceUnavailable

SCHEDULE = "dj_hyperview.contrib.database.signals._schedule_invalidation"
INVALIDATE = "dj_hyperview.contrib.database._invalidation.invalidate_templates"
pytestmark = [
    pytest.mark.skipif(
        not apps.is_installed("django.contrib.admin"),
        reason="optional admin settings are not active",
    ),
    pytest.mark.django_db(transaction=True),
]


def _model() -> type[Any]:
    return apps.get_model("dj_hyperview_database", "HyperviewTemplate")


def _urls(template: Any) -> tuple[str, str, str]:
    opts = template._meta
    prefix = f"admin:{opts.app_label}_{opts.model_name}"
    return (
        reverse(f"{prefix}_changelist"),
        reverse(f"{prefix}_change", args=[template.pk]),
        reverse(f"{prefix}_delete", args=[template.pk]),
    )


def _form(
    name: str, content: str, revision: int | None = None, *, active: bool = True
) -> dict[str, str]:
    data = {"name": name, "content": content, "_save": "Save"}
    if active:
        data["active"] = "on"
    if revision is not None:
        data["expected_revision"] = str(revision)
    return data


def test_loaddata_preserves_recoverable_legacy_name(tmp_path) -> None:
    """Raw fixture loading accepts legacy names and restores their identity."""
    legacy_name = "legacy\\name.xml"
    fixture = tmp_path / "legacy-template.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "model": "dj_hyperview_database.hyperviewtemplate",
                    "pk": 9001,
                    "fields": {
                        "name": legacy_name,
                        "name_identity": "stale",
                        "content": "<view />",
                        "active": True,
                        "revision": 1,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    with patch(SCHEDULE) as schedule:
        call_command("loaddata", fixture, verbosity=0)

    template = _model()._base_manager.get(pk=9001)
    assert template.name == legacy_name
    assert template.name_identity == template_name_identity(legacy_name)
    schedule.assert_not_called()


def test_admin_create_and_edit_publish_once_with_hidden_revision(admin_client) -> None:
    model = _model()
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")
    with patch(SCHEDULE) as schedule:
        added = admin_client.post(add, _form("old.xml", "<old />"))
        template = model.objects.get()
        changelist, change, _ = _urls(template)
        page = admin_client.get(change)
        changed = admin_client.post(
            change, _form("new.xml", "<new />", 1, active=False)
        )

    assert (added.status_code, added.headers["Location"]) == (302, changelist)
    assert b'type="hidden" name="expected_revision" value="1"' in page.content
    assert (changed.status_code, changed.headers["Location"]) == (302, changelist)
    template.refresh_from_db()
    assert (template.name, template.content, template.active, template.revision) == (
        "new.xml",
        "<new />",
        False,
        2,
    )
    assert schedule.call_args_list == [
        call("old.xml", using="default"),
        call("old.xml", "new.xml", using="default"),
    ]


def test_admin_add_never_overwrites_a_concurrently_created_template(
    admin_client,
) -> None:
    """An Add race preserves the row that won the unique-name race."""
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    model = _model()
    existing = model.objects.create(name="screen.xml", content="<winner />")
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")

    with (
        patch.object(
            module.HyperviewTemplateAdminForm,
            "clean_name",
            return_value="screen.xml",
        ),
        patch.object(model, "validate_unique", return_value=None),
    ):
        response = admin_client.post(
            add,
            _form("screen.xml", "<loser />"),
            follow=True,
        )

    existing.refresh_from_db()
    assert response.status_code == 200
    assert b"Template changed; reload and retry." in response.content
    assert model.objects.count() == 1
    assert (existing.content, existing.revision) == ("<winner />", 1)


def test_admin_stale_edit_is_a_redacted_form_error(admin_client) -> None:
    model = _model()
    template = model.objects.create(name="screen.xml", content="<one />")
    _, change, _ = _urls(template)
    model._base_manager.filter(pk=template.pk).update(content="<winner />", revision=2)

    response = admin_client.post(change, _form("screen.xml", "<stale />", 1))

    errors = response.context["adminform"].form.errors.as_data()
    stored = model.objects.get(pk=template.pk)
    assert response.status_code == 200
    assert errors["__all__"][0].code == "publication_conflict"
    assert str(errors["__all__"][0].message) == "Template changed; reload and retry."
    assert (stored.content, stored.revision) == ("<winner />", 2)
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    with patch.object(module, "rename_template", side_effect=PublicationConflict):
        raced = admin_client.post(
            change, _form("screen.xml", "<lost />", 2), follow=True
        )
    assert b"Template changed; reload and retry." in raced.content


@pytest.mark.parametrize("operation", ["change", "delete"])
def test_conflict_redirect_preserves_admin_query_context(
    admin_client, operation: str
) -> None:
    """Conflict redirects retain popup and filtered-changelist state."""
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    template = _model().objects.create(name="screen.xml", content="<old />")
    _, change, delete = _urls(template)
    query = "?_popup=1&_changelist_filters=active__exact%3D1"
    url = (change if operation == "change" else delete) + query
    data = (
        _form("screen.xml", "<lost />", 1)
        if operation == "change"
        else {"post": "yes", "expected_revision": "1"}
    )
    service = "rename_template" if operation == "change" else "delete_template"

    with patch.object(module, service, side_effect=PublicationConflict):
        response = admin_client.post(url, data)

    assert response.status_code == 302
    assert response.headers["Location"] == url


def test_admin_delete_rejects_stale_revision_and_then_deletes(admin_client) -> None:
    model = _model()
    template = model.objects.create(name="screen.xml", content="<view />")
    changelist, _, delete = _urls(template)
    confirmation = admin_client.get(delete)
    assert b'name="expected_revision" value="1"' in confirmation.content
    model._base_manager.filter(pk=template.pk).update(revision=2)
    invalid = admin_client.post(delete, {"post": "yes"}, follow=True)
    assert b"Template changed; reload and retry." in invalid.content

    stale = admin_client.post(
        delete,
        {"post": "yes", "expected_revision": "1"},
        follow=True,
    )

    assert stale.status_code == 200
    assert b"Template changed; reload and retry." in stale.content
    assert model.objects.filter(pk=template.pk, revision=2).exists()
    current = admin_client.post(delete, {"post": "yes", "expected_revision": "2"})
    assert (current.status_code, current.headers["Location"]) == (302, changelist)
    assert model.objects.count() == 0


def test_delete_confirmation_uses_django_translation_and_button_contract(
    admin_client,
) -> None:
    """The override preserves Django's translated prompt and cancel semantics."""
    template = _model().objects.create(name="screen.xml", content="<view />")
    _, _, delete = _urls(template)
    message = (
        "Are you sure you want to delete the %(object_name)s “%(escaped_object)s”? "
        "All of the following related items will be deleted:"
    )

    with translation.override("es-ar"):
        response = admin_client.get(delete)
        expected = translation.gettext(message) % {
            "object_name": response.context["object_name"],
            "escaped_object": response.context["escaped_object"],
        }

    rendered = response.content.decode()
    assert expected in rendered
    assert '<a role="button" href="#" class="button cancel-link">' in rendered


def test_delete_confirmation_honors_maximum_display_zero(admin_client) -> None:
    """A zero display limit hides the related-object list like Django does."""
    model = _model()
    template = model.objects.create(name="screen.xml", content="<view />")
    _, _, delete = _urls(template)
    registered = admin.site.get_model_admin(model)

    with patch.object(registered, "delete_confirmation_max_display", 0):
        response = admin_client.get(delete)

    rendered = response.content.decode()
    assert "<h2>Objects</h2>" not in rendered
    assert 'id="deleted-objects"' not in rendered


def test_delete_revision_survives_an_overridden_admin_template(
    admin_client, tmp_path
) -> None:
    """The revision field does not depend on Django's rendered input bytes."""
    parent = tmp_path / "admin" / "delete_confirmation.html"
    parent.parent.mkdir()
    parent.write_text(
        """{% extends "admin/base_site.html" %}
{% block content %}
{% block delete_confirm %}
<form method="post">{% csrf_token %}
<input type="hidden" name="post" value="yes" />
<input type="submit" value="Confirm" />
</form>
{% endblock %}
{% endblock %}
""",
        encoding="utf-8",
    )
    templates = [{**settings.TEMPLATES[0], "DIRS": [tmp_path]}]
    template = _model().objects.create(name="screen.xml", content="<view />")
    _, _, delete = _urls(template)

    with override_settings(TEMPLATES=templates):
        confirmation = admin_client.get(delete)

    assert b'name="expected_revision" value="1"' in confirmation.content


def test_admin_can_delete_an_invalid_legacy_name(admin_client) -> None:
    """Legacy rows remain recoverable through the guarded admin delete flow."""
    model = _model()
    model._base_manager.bulk_create([model(name=r"bad\name.xml", content="<view />")])
    template = model._base_manager.get()
    changelist, _, delete = _urls(template)

    confirmation = admin_client.get(delete)
    response = admin_client.post(
        delete, {"post": "yes", "expected_revision": str(template.revision)}
    )

    assert b'name="expected_revision" value="1"' in confirmation.content
    assert (response.status_code, response.headers["Location"]) == (302, changelist)
    assert model._base_manager.count() == 0


def test_admin_can_rename_an_invalid_legacy_name(admin_client) -> None:
    """A canonical edit repairs a historical name through the change view."""
    model = _model()
    legacy_name = "bad\\name.xml"
    model._base_manager.bulk_create(
        [
            model(
                name=legacy_name,
                name_identity=template_name_identity(legacy_name),
                content="<view />",
            )
        ]
    )
    template = model._base_manager.get()
    changelist, change, _ = _urls(template)

    response = admin_client.post(
        change,
        _form("repaired.xml", "<repaired />", template.revision),
    )

    assert (response.status_code, response.headers["Location"]) == (302, changelist)
    template.refresh_from_db()
    assert (template.name, template.content, template.revision) == (
        "repaired.xml",
        "<repaired />",
        2,
    )


def test_admin_bulk_delete_uses_one_batch_invalidation(admin_client) -> None:
    model = _model()
    rows = [
        model.objects.create(name=f"{name}.xml", content="<view />")
        for name in ("one", "two")
    ]
    changelist, _, _ = _urls(rows[0])
    data = {
        "action": "delete_selected",
        "_selected_action": [str(row.pk) for row in rows],
        "post": "yes",
    }
    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        response = admin_client.post(changelist, data)

    assert response.status_code == 302
    assert model.objects.count() == 0
    schedule.assert_called_once_with("one.xml", "two.xml", using="default")


def test_admin_transaction_outcomes_preserve_database_truth(admin_client) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    model = _model()
    template = model.objects.create(name="screen.xml", content="<old />")
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")
    _, change, _ = _urls(template)
    with (
        patch(INVALIDATE, side_effect=SourceUnavailable("cache:test", "failure")),
        pytest.raises(SourceUnavailable, match="failure"),
    ):
        admin_client.post(add, _form("committed.xml", "<view />"))
    assert model.objects.get(name="committed.xml").revision == 1

    with (
        patch.object(
            module.HyperviewTemplateAdmin,
            "log_change",
            side_effect=RuntimeError("rollback"),
        ),
        patch(INVALIDATE) as invalidate,
        pytest.raises(RuntimeError, match="rollback"),
    ):
        admin_client.post(change, _form("screen.xml", "<new />", 1))

    template.refresh_from_db()
    assert (template.content, template.revision) == ("<old />", 1)
    invalidate.assert_not_called()


def test_add_form_excludes_revision_control_from_get_and_post(admin_client) -> None:
    model = _model()
    add = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")

    page = admin_client.get(add)
    form = page.context["adminform"].form
    assert "expected_revision" not in form.fields
    assert b'name="expected_revision"' not in page.content

    data = _form("screen.xml", "<view />")
    data["expected_revision"] = ["7", "9"]
    response = admin_client.post(add, data)
    assert response.status_code == 302
    template = model.objects.get()
    assert (template.name, template.revision) == ("screen.xml", 1)


@pytest.mark.parametrize("values", [["999", "1"], ["1", "999"], ["1", "1"]])
def test_admin_change_rejects_duplicate_revision_values(
    admin_client, values: list[str]
) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    model = _model()
    template = model.objects.create(name="screen.xml", content="<old />")
    _, change, _ = _urls(template)
    data = _form("screen.xml", "<lost />")
    data["expected_revision"] = values

    with (
        patch.object(module, "rename_template", wraps=module.rename_template) as rename,
        patch(SCHEDULE) as schedule,
    ):
        response = admin_client.post(change, data, follow=True)

    template.refresh_from_db()
    assert b"Template changed; reload and retry." in response.content
    assert (template.content, template.revision) == ("<old />", 1)
    rename.assert_not_called()
    schedule.assert_not_called()


@pytest.mark.parametrize("values", [["999", "1"], ["1", "999"], ["1", "1"]])
def test_admin_delete_rejects_duplicate_revision_values(
    admin_client, values: list[str]
) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    model = _model()
    template = model.objects.create(name="screen.xml", content="<view />")
    _, _, delete = _urls(template)

    with (
        patch.object(
            module, "delete_template", wraps=module.delete_template
        ) as delete_api,
        patch(SCHEDULE) as schedule,
    ):
        response = admin_client.post(
            delete, {"post": "yes", "expected_revision": values}, follow=True
        )

    assert b"Template changed; reload and retry." in response.content
    assert model.objects.filter(pk=template.pk).exists()
    delete_api.assert_not_called()
    schedule.assert_not_called()


@pytest.mark.parametrize("operation", ["change", "delete"])
@pytest.mark.parametrize(
    "token", ["9" * 5_000, "\u0669" * 5_000], ids=["ascii", "unicode"]
)
def test_admin_rejects_oversized_revision_before_publication(
    admin_client, operation: str, token: str
) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    model = _model()
    template = model.objects.create(name="screen.xml", content="<old />")
    _, change, delete = _urls(template)
    url = change if operation == "change" else delete
    data = _form("screen.xml", "<lost />") if operation == "change" else {"post": "yes"}
    data["expected_revision"] = token
    service_name = "rename_template" if operation == "change" else "delete_template"

    with (
        patch.object(
            module, service_name, wraps=getattr(module, service_name)
        ) as service,
        patch(SCHEDULE) as schedule,
    ):
        response = admin_client.post(url, data, follow=True)

    template.refresh_from_db()
    assert b"Template changed; reload and retry." in response.content
    assert (template.content, template.revision) == ("<old />", 1)
    service.assert_not_called()
    schedule.assert_not_called()


def test_admin_revision_parser_honors_database_range(admin_client) -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    maximum = connections[DEFAULT_DB_ALIAS].ops.integer_field_range(
        "PositiveIntegerField"
    )[1]
    assert maximum is not None
    model = _model()

    editable = model.objects.create(name="edit.xml", content="<old />")
    model._base_manager.filter(pk=editable.pk).update(revision=maximum - 1)
    _, change, _ = _urls(editable)
    changed = admin_client.post(change, _form("edit.xml", "<new />", maximum - 1))
    editable.refresh_from_db()
    assert changed.status_code == 302
    assert (editable.content, editable.revision) == ("<new />", maximum)

    deletable = model.objects.create(name="delete.xml", content="<view />")
    model._base_manager.filter(pk=deletable.pk).update(revision=maximum)
    _, _, delete = _urls(deletable)
    deleted = admin_client.post(
        delete, {"post": "yes", "expected_revision": str(maximum)}
    )
    assert deleted.status_code == 302
    assert not model.objects.filter(pk=deletable.pk).exists()

    rejected = model.objects.create(name="reject.xml", content="<view />")
    _, _, reject_delete = _urls(rejected)
    with (
        patch.object(
            module, "delete_template", wraps=module.delete_template
        ) as delete_api,
        patch(SCHEDULE) as schedule,
    ):
        response = admin_client.post(
            reject_delete,
            {"post": "yes", "expected_revision": str(maximum + 1)},
            follow=True,
        )
    assert b"Template changed; reload and retry." in response.content
    assert model.objects.filter(pk=rejected.pk).exists()
    delete_api.assert_not_called()
    schedule.assert_not_called()


def test_admin_form_public_overrides_have_complete_docstrings() -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    init_doc = module.HyperviewTemplateAdminForm.__init__.__doc__ or ""
    clean_doc = module.HyperviewTemplateAdminForm.clean.__doc__ or ""

    assert "Args:" in init_doc
    assert "Returns:" in clean_doc
    assert "Raises:" in clean_doc
