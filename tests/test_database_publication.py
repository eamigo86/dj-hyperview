"""Validated database publication service integration tests."""

import traceback
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import (
    DatabaseError,
    IntegrityError,
    connections,
    transaction,
)
from django.db.models import Model, QuerySet
from django.test import override_settings

from dj_hyperview.contrib.database.services import (
    PublicationConflict,
    PublicationResult,
    publish_template,
)
from dj_hyperview.exceptions import InvalidTemplateName, SourceUnavailable
from tests.test_database_app import run_isolated

ALIASES = ("default", "replica")
DATABASE_APPS = ["dj_hyperview", "dj_hyperview.contrib.database"]
SCHEDULE = "dj_hyperview.contrib.database.signals._schedule_invalidation"
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
def publication_model(django_db_blocker: Any) -> Iterator[type[Model]]:
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


def test_service_module_is_lazy_before_optional_app_setup() -> None:
    result = run_isolated(
        "tests.settings",
        "import sys, dj_hyperview.contrib.database.services; "
        "print('dj_hyperview.contrib.database.models' in sys.modules)",
    )
    assert (result.returncode, result.stdout.strip()) == (0, "False")
    with pytest.raises(SourceUnavailable, match="app unavailable"):
        publish_template("screen.xml", "<view />")


def test_publish_creates_validated_inactive_template_and_immutable_result(
    publication_model: type[Model],
) -> None:
    with patch(SCHEDULE) as schedule:
        result = publish_template("screen.xml", "<view />", active=False)
    stored = publication_model.objects.get(name="screen.xml")
    assert result == PublicationResult("screen.xml", 1, True)
    assert (stored.content, stored.active, stored.revision) == ("<view />", False, 1)
    with pytest.raises(FrozenInstanceError):
        result.revision = 2
    schedule.assert_called_once_with("screen.xml", using="default")


def test_publish_updates_revision_and_enforces_expected_revision(
    publication_model: type[Model],
) -> None:
    publication_model.objects.create(name="screen.xml", content="<one />")
    with patch(SCHEDULE) as schedule:
        latest = publish_template("screen.xml", "<two />")
        with pytest.raises(PublicationConflict, match="Template publication conflict"):
            publish_template("screen.xml", "<stale />", expected_revision=1)
        matched = publish_template(
            "screen.xml", "<three />", expected_revision=latest.revision
        )

    stored = publication_model.objects.get(name="screen.xml")
    assert latest == PublicationResult("screen.xml", 2, False)
    assert matched == PublicationResult("screen.xml", 3, False)
    assert (stored.content, stored.revision) == ("<three />", 3)
    assert schedule.call_count == 2


def test_missing_expected_revision_conflicts_without_write(
    publication_model: type[Model],
) -> None:
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(PublicationConflict),
    ):
        publish_template("missing.xml", "<view />", expected_revision=1)

    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("name", "content", "error_type"),
    [
        ("../private.xml", "<view />", InvalidTemplateName),
        ("screen.xml", "<!DOCTYPE view><view />", ValidationError),
    ],
)
def test_invalid_publication_never_persists(
    publication_model: type[Model],
    name: str,
    content: str,
    error_type: type[Exception],
) -> None:
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(error_type),
    ):
        publish_template(name, content)

    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


@pytest.mark.parametrize("expected", [True, 0, -1, "1"])
def test_invalid_expected_revision_fails_before_database_access(
    expected: object,
) -> None:
    with (
        patch("dj_hyperview.contrib.database.services.transaction.atomic") as atomic,
        pytest.raises(ValueError, match="expected_revision"),
    ):
        publish_template("screen.xml", "<view />", expected_revision=expected)

    atomic.assert_not_called()


def test_publication_validates_then_saves_once_under_row_lock(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = publication_model.objects.create(name="screen.xml", content="<old />")
    events: list[str] = []
    original_lock = QuerySet.select_for_update
    original_clean = publication_model.full_clean
    original_save = publication_model.save

    def lock(queryset: QuerySet, *args: object, **kwargs: object) -> QuerySet:
        events.append("lock")
        return original_lock(queryset, *args, **kwargs)

    def clean(instance: Model, *args: object, **kwargs: object) -> None:
        events.append("clean")
        original_clean(instance, *args, **kwargs)

    def save(instance: Model, *args: object, **kwargs: object) -> None:
        events.append("save")
        original_save(instance, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", lock)
    monkeypatch.setattr(publication_model, "full_clean", clean)
    monkeypatch.setattr(publication_model, "save", save)

    assert publish_template("screen.xml", "<new />").revision == 2
    assert events == ["lock", "clean", "save"]
    assert publication_model.objects.get(pk=template.pk).content == "<new />"


def test_publish_selects_write_router_once_and_honors_explicit_alias(
    publication_model: type[Model],
) -> None:
    router = _WriteRouter("replica")
    with override_settings(DATABASE_ROUTERS=[router]):
        routed = publish_template("routed.xml", "<replica />")
        explicit = publish_template("explicit.xml", "<default />", using="default")
        router.alias = "missing"
        with pytest.raises(SourceUnavailable, match="alias unavailable"):
            publish_template("invalid-router.xml", "<view />")

    assert routed == PublicationResult("routed.xml", 1, True)
    assert explicit == PublicationResult("explicit.xml", 1, True)
    assert router.calls == 2
    assert publication_model.objects.using("replica").get().name == "routed.xml"
    assert publication_model.objects.using("default").get().name == "explicit.xml"


@pytest.mark.parametrize("using", [True, "", "missing"])
def test_invalid_database_alias_fails_safely(using: object) -> None:
    with pytest.raises(SourceUnavailable) as captured:
        publish_template("screen.xml", "<view />", using=using)

    assert (
        str(captured.value)
        == "Template source unavailable: database (alias unavailable)"
    )
    assert (captured.value.__cause__, captured.value.__context__) == (None, None)


def test_create_race_becomes_redacted_publication_conflict(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    publication_model.objects.create(name="screen.xml", content="<winner />")
    original_get = QuerySet.get
    lookup_count = 0

    def miss_existing_once(
        queryset: QuerySet, *args: object, **kwargs: object
    ) -> Model:
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            raise publication_model.DoesNotExist
        return original_get(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "get", miss_existing_once)
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(PublicationConflict) as captured,
    ):
        publish_template("screen.xml", "<private />")

    rendered = "".join(traceback.format_exception(captured.value))
    assert str(captured.value) == "Template publication conflict"
    assert (captured.value.__cause__, captured.value.__context__) == (None, None)
    assert "UNIQUE constraint failed" not in rendered
    assert publication_model._meta.db_table not in rendered
    assert publication_model.objects.get(name="screen.xml").content == "<winner />"
    schedule.assert_not_called()


def test_unverified_create_integrity_error_remains_database_error(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = IntegrityError("private create infrastructure failure")

    def fail_save(instance: Model, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs
        raise failure

    monkeypatch.setattr(publication_model, "save", fail_save)
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(IntegrityError) as captured,
    ):
        publish_template("screen.xml", "<private />")

    assert captured.value is failure
    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


def test_update_integrity_error_remains_database_error(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    publication_model.objects.create(name="screen.xml", content="<original />")
    failure = IntegrityError("private update infrastructure failure")

    def fail_save(instance: Model, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs
        raise failure

    monkeypatch.setattr(publication_model, "save", fail_save)
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(IntegrityError) as captured,
    ):
        publish_template("screen.xml", "<changed />")

    stored = publication_model.objects.get(name="screen.xml")
    assert captured.value is failure
    assert (stored.content, stored.revision) == ("<original />", 1)
    schedule.assert_not_called()


def test_sqlite_update_constraint_error_is_not_a_publication_conflict(
    publication_model: type[Model],
) -> None:
    connection = connections["default"]
    if connection.vendor != "sqlite":
        pytest.skip("SQLite trigger regression")
    publication_model.objects.create(name="screen.xml", content="<original />")
    table = connection.ops.quote_name(publication_model._meta.db_table)
    trigger = connection.ops.quote_name("djhv_publication_update_abort")
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'update blocked'); END"
        )
    try:
        with (
            patch(SCHEDULE) as schedule,
            pytest.raises(IntegrityError, match="update blocked"),
        ):
            publish_template("screen.xml", "<changed />")
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER {trigger}")

    stored = publication_model.objects.get(name="screen.xml")
    assert (stored.content, stored.revision) == ("<original />", 1)
    schedule.assert_not_called()


def test_database_failure_and_outer_rollback_leave_no_publication(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_save(instance: Model, *args: object, **kwargs: object) -> None:
        del instance, args, kwargs
        raise DatabaseError("write failed")

    monkeypatch.setattr(publication_model, "save", fail_save)
    with pytest.raises(DatabaseError, match="write failed"):
        publish_template("failed.xml", "<view />")
    monkeypatch.undo()

    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            publish_template("rolled-back.xml", "<view />")
            raise RuntimeError("rollback")

    assert publication_model.objects.count() == 0


def test_cache_failure_after_commit_keeps_published_database_row(
    publication_model: type[Model],
) -> None:
    with (
        patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates",
            side_effect=SourceUnavailable("cache:test", "backend failure"),
        ),
        pytest.raises(SourceUnavailable, match="backend failure"),
    ):
        publish_template("screen.xml", "<committed />")

    assert publication_model.objects.get(name="screen.xml").content == "<committed />"
