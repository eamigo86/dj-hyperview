"""Conflict-safe database admin publication integration tests."""

import importlib
from typing import Any
from unittest.mock import call, patch

import pytest
from django.apps import apps
from django.urls import reverse

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


def test_admin_bulk_delete_remains_signal_backed(admin_client) -> None:
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
    with patch(SCHEDULE) as schedule:
        response = admin_client.post(changelist, data)

    assert response.status_code == 302
    assert model.objects.count() == 0
    assert {item.args for item in schedule.call_args_list} == {
        ("one.xml",),
        ("two.xml",),
    }


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


def test_admin_form_public_overrides_have_complete_docstrings() -> None:
    module = importlib.import_module("dj_hyperview.contrib.database.admin")
    init_doc = module.HyperviewTemplateAdminForm.__init__.__doc__ or ""
    clean_doc = module.HyperviewTemplateAdminForm.clean.__doc__ or ""

    assert "Args:" in init_doc
    assert "Returns:" in clean_doc
    assert "Raises:" in clean_doc
