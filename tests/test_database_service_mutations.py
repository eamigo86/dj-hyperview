"""Rename and delete publication service integration tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import (
    DatabaseError,
    IntegrityError,
    InterfaceError,
    connections,
    transaction,
)
from django.db.models import Model, QuerySet
from django.test import override_settings

from dj_hyperview.contrib.database.services import (
    PublicationConflict,
    PublicationResult,
    delete_template,
    publish_template,
    rename_template,
)
from dj_hyperview.contrib.database.sources import DatabaseSource
from dj_hyperview.exceptions import InvalidTemplateName, SourceUnavailable

ALIASES = ("default", "replica")
DATABASE_APPS = ["dj_hyperview", "dj_hyperview.contrib.database"]
SCHEDULE = "dj_hyperview.contrib.database.signals._schedule_invalidation"
INVALIDATE = "dj_hyperview.contrib.database._invalidation.invalidate_templates"
pytestmark = pytest.mark.django_db(transaction=True, databases=ALIASES)


class _WriteRouter:
    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.calls = 0

    def db_for_write(self, model: type[Model], **hints: object) -> str:
        del model, hints
        self.calls += 1
        return self.alias


@pytest.fixture
def mutation_model(django_db_blocker: Any) -> Iterator[type[Model]]:
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
        with django_db_blocker.unblock():
            for alias in ALIASES:
                with connections[alias].schema_editor() as editor:
                    editor.create_model(model)
        yield model
        with django_db_blocker.unblock():
            for alias in reversed(ALIASES):
                with connections[alias].schema_editor() as editor:
                    editor.delete_model(model)


def test_rename_updates_optional_fields_and_revision_once(
    mutation_model: type[Model],
) -> None:
    source = mutation_model.objects.create(
        name="old.xml", content="<old />", active=True, revision=3
    )

    with patch(SCHEDULE) as schedule:
        result = rename_template(
            "old.xml",
            "new.xml",
            content="<new />",
            active=False,
            expected_revision=3,
        )
        preserved = rename_template("new.xml", "new.xml", expected_revision=4)

    stored = mutation_model.objects.get(pk=source.pk)
    assert result == PublicationResult("new.xml", 4, False)
    assert preserved == PublicationResult("new.xml", 5, False)
    assert (stored.name, stored.content, stored.active, stored.revision) == (
        "new.xml",
        "<new />",
        False,
        5,
    )
    assert schedule.call_count == 2
    schedule.assert_any_call("old.xml", "new.xml", using="default")
    schedule.assert_any_call("new.xml", using="default")


def test_rename_conflicts_leave_source_and_target_unchanged(
    mutation_model: type[Model],
) -> None:
    source = mutation_model.objects.create(name="old.xml", content="<old />")
    target = mutation_model.objects.create(name="taken.xml", content="<taken />")
    with patch(SCHEDULE) as schedule:
        for current, new, expected in [
            ("missing.xml", "new.xml", None),
            ("old.xml", "new.xml", 9),
        ]:
            with pytest.raises(PublicationConflict):
                rename_template(current, new, expected_revision=expected)
        with pytest.raises(PublicationConflict) as target_conflict:
            rename_template("old.xml", "taken.xml", expected_revision=1)

    assert mutation_model.objects.get(pk=source.pk).name == "old.xml"
    assert mutation_model.objects.get(pk=target.pk).content == "<taken />"
    assert str(target_conflict.value) == "Template publication conflict"
    assert (target_conflict.value.__cause__, target_conflict.value.__context__) == (
        None,
        None,
    )
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("current", "new", "expected", "error"),
    [
        ("../private.xml", "safe.xml", None, InvalidTemplateName),
        ("safe.xml", "../private.xml", None, InvalidTemplateName),
        ("safe.xml", "new.xml", True, ValueError),
    ],
)
def test_rename_validates_all_inputs_before_database_effects(
    current: str,
    new: str,
    expected: object,
    error: type[Exception],
) -> None:
    with (
        patch("dj_hyperview.contrib.database.services.transaction.atomic") as atomic,
        pytest.raises(error),
    ):
        rename_template(current, new, expected_revision=expected)
    atomic.assert_not_called()


def test_mutation_boundary_rejects_unknown_alias_and_missing_app() -> None:
    with pytest.raises(SourceUnavailable, match="alias unavailable"):
        rename_template("old.xml", "new.xml", using="missing")
    with pytest.raises(SourceUnavailable, match="app unavailable"):
        delete_template("old.xml")


def test_rename_validation_error_rolls_back_without_invalidation(
    mutation_model: type[Model],
) -> None:
    mutation_model.objects.create(name="old.xml", content="<old />")
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(ValidationError),
    ):
        rename_template("old.xml", "new.xml", content="<!DOCTYPE view><view />")

    stored = mutation_model.objects.get()
    assert (stored.name, stored.content, stored.revision) == ("old.xml", "<old />", 1)
    schedule.assert_not_called()


def test_delete_existing_missing_and_revision_contract(
    mutation_model: type[Model],
) -> None:
    mutation_model.objects.create(name="delete.xml", content="<delete />", revision=2)
    with patch(SCHEDULE) as schedule:
        with pytest.raises(PublicationConflict):
            delete_template("delete.xml", expected_revision=1)
        assert delete_template("delete.xml", expected_revision=2) is True
        assert delete_template("missing.xml") is False
        with pytest.raises(PublicationConflict):
            delete_template("missing.xml", expected_revision=1)

    assert mutation_model.objects.count() == 0
    schedule.assert_called_once_with("delete.xml", using="default")


def test_mutations_select_write_router_once_and_honor_explicit_alias(
    mutation_model: type[Model],
) -> None:
    mutation_model.objects.using("replica").create(name="routed.xml", content="<r />")
    mutation_model.objects.create(name="explicit.xml", content="<e />")
    router = _WriteRouter("replica")
    with override_settings(DATABASE_ROUTERS=[router]):
        renamed = rename_template("routed.xml", "renamed.xml")
        deleted = delete_template("explicit.xml", using="default")

    assert renamed.name == "renamed.xml"
    assert deleted is True
    assert router.calls == 1
    assert mutation_model.objects.using("replica").get().name == "renamed.xml"
    assert not mutation_model.objects.using("default").exists()


@pytest.mark.parametrize("error_type", [IntegrityError, DatabaseError])
def test_rename_preserves_unverified_database_error(
    mutation_model: type[Model],
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[DatabaseError],
) -> None:
    mutation_model.objects.create(name="old.xml", content="<old />")
    failure = error_type("private infrastructure failure")

    def fail_save(instance: Model, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs
        raise failure

    monkeypatch.setattr(mutation_model, "save", fail_save)
    with pytest.raises(error_type) as captured:
        rename_template("old.xml", "new.xml")

    assert captured.value is failure
    assert mutation_model.objects.get().name == "old.xml"


def test_case_equivalent_target_does_not_mask_unrelated_integrity_error(
    mutation_model: type[Model],
) -> None:
    connection = connections["default"]
    if connection.vendor != "sqlite":
        pytest.skip("SQLite NOCASE regression")
    name_field = mutation_model._meta.get_field("name")
    original_collation = name_field.db_collation
    with connection.schema_editor() as editor:
        editor.delete_model(mutation_model)
    name_field.db_collation = "nocase"
    try:
        with connection.schema_editor() as editor:
            editor.create_model(mutation_model)
    finally:
        name_field.db_collation = original_collation

    mutation_model.objects.create(name="screen.xml", content="<old />")
    table = connection.ops.quote_name(mutation_model._meta.db_table)
    trigger = connection.ops.quote_name("djhv_rename_unrelated_abort")
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'unrelated rename failure'); END"
        )
    try:
        with pytest.raises(IntegrityError, match="unrelated rename failure"):
            rename_template("screen.xml", "Screen.xml")
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER {trigger}")

    stored = mutation_model.objects.get()
    assert (stored.name, stored.revision) == ("screen.xml", 1)


def test_case_variants_remain_distinct_under_case_insensitive_collation(
    mutation_model: type[Model],
) -> None:
    """Package identity stays byte-exact when the database collation does not."""
    connection = connections["default"]
    if connection.vendor != "sqlite":
        pytest.skip("SQLite NOCASE regression")
    name_field = mutation_model._meta.get_field("name")
    original_collation = name_field.db_collation
    with connection.schema_editor() as editor:
        editor.delete_model(mutation_model)
    name_field.db_collation = "nocase"
    try:
        with connection.schema_editor() as editor:
            editor.create_model(mutation_model)
    finally:
        name_field.db_collation = original_collation

    upper = publish_template("Home.xml", "<upper />", using="default")
    lower = publish_template("home.xml", "<lower />", using="default")
    source = DatabaseSource(using="default")

    assert (upper.created, lower.created) == (True, True)
    assert mutation_model.objects.count() == 2
    assert source.resolve("Home.xml").content == "<upper />"
    assert source.resolve("home.xml").content == "<lower />"


@pytest.mark.parametrize("primary_type", [IntegrityError, DatabaseError])
def test_rename_proof_failure_preserves_primary_database_error(
    mutation_model: type[Model],
    monkeypatch: pytest.MonkeyPatch,
    primary_type: type[DatabaseError],
) -> None:
    primary = primary_type("primary write failure")
    secondary = InterfaceError("secondary proof failure")
    mutation_model.objects.create(name="old.xml", content="<old />")

    def fail_save(instance: Model, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs
        raise primary

    def fail_exists(queryset: QuerySet) -> bool:
        del queryset
        raise secondary

    monkeypatch.setattr(mutation_model, "save", fail_save)
    monkeypatch.setattr(QuerySet, "exists", fail_exists)
    with pytest.raises(primary_type) as captured:
        rename_template("old.xml", "new.xml")

    assert captured.value is primary


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_concurrent_source_deletion_becomes_conflict_without_recreation(
    mutation_model: type[Model], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def stale_locked_row(queryset: Any, *args: object, **kwargs: object) -> Model:
        del queryset, args, kwargs
        return mutation_model(pk=99, name="old.xml", content="<old />")

    monkeypatch.setattr(mutation_model.objects.all().__class__, "get", stale_locked_row)
    with pytest.raises(PublicationConflict):
        if operation == "rename":
            rename_template("old.xml", "new.xml")
        else:
            delete_template("old.xml")

    assert mutation_model.objects.count() == 0


def test_zero_row_delete_discards_invalidation_before_conflict(
    mutation_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    def stale_locked_row(queryset: QuerySet, *args: object, **kwargs: object) -> Model:
        del queryset, args, kwargs
        return mutation_model(pk=99, name="old.xml", content="<old />")

    monkeypatch.setattr(QuerySet, "get", stale_locked_row)
    with (
        patch(
            INVALIDATE, side_effect=SourceUnavailable("cache:test", "failure")
        ) as invalidate,
        pytest.raises(PublicationConflict),
    ):
        delete_template("old.xml")

    invalidate.assert_not_called()


def test_rollback_and_postcommit_cache_failure_keep_database_truth(
    mutation_model: type[Model],
) -> None:
    mutation_model.objects.create(name="old.xml", content="<old />")
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            rename_template("old.xml", "rolled.xml")
            raise RuntimeError("rollback")
    assert mutation_model.objects.get().name == "old.xml"

    with (
        patch(INVALIDATE, side_effect=SourceUnavailable("cache:test", "failure")),
        pytest.raises(SourceUnavailable, match="failure"),
    ):
        delete_template("old.xml")
    assert mutation_model.objects.count() == 0
