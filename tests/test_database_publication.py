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
    """Create the template table on every alias for one test.

    Args:
        django_db_blocker: Allows the fixture to create the tables outside a test
        transaction.

    Yields:
        The template model class, with its table created on every alias.
    """
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
    """Importing the services module does not load the models, and publishing fails
    while the app is unavailable.
    """
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
    """Publishing creates a validated inactive template and returns an immutable result.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
    with patch(SCHEDULE) as schedule:
        result = publish_template("screen.xml", "<view />", active=False)
    stored = publication_model.objects.get(name="screen.xml")
    assert result == PublicationResult("screen.xml", 1, True)
    assert (stored.content, stored.active, stored.revision) == ("<view />", False, 1)
    with pytest.raises(FrozenInstanceError):
        result.revision = 2
    schedule.assert_called_once_with("screen.xml", using="default")


def test_publish_keeps_the_existing_activity_unless_it_is_given(
    publication_model: type[Model],
) -> None:
    """Publishing keeps the existing activity flag unless one is given.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
    publication_model.objects.create(name="screen.xml", content="<one />", active=False)
    with patch(SCHEDULE):
        publish_template("screen.xml", "<two />")
        kept = publication_model.objects.get(name="screen.xml").active
        publish_template("screen.xml", "<three />", active=True)
        activated = publication_model.objects.get(name="screen.xml").active
        publish_template("created.xml", "<view />")

    assert (kept, activated) == (False, True)
    assert publication_model.objects.get(name="created.xml").active is True


def test_publish_updates_revision_and_enforces_expected_revision(
    publication_model: type[Model],
) -> None:
    """Publishing bumps the revision and enforces the expected revision.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
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
    """An expected revision for a missing template conflicts without writing.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
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
        (
            "screen.xml",
            '<?xml version="1.0" encoding="UTF-8\x00"?><view />',
            ValidationError,
        ),
        (
            "screen.xml",
            '<?xml version="1.0" encoding="UTF-8\U0000d800"?><view />',
            ValidationError,
        ),
    ],
)
def test_invalid_publication_never_persists(
    publication_model: type[Model],
    name: str,
    content: str,
    error_type: type[Exception],
) -> None:
    """An invalid name or content is never persisted.

    Args:
        publication_model: Template model class whose table exists on every alias.
        name: Template name passed to publish_template.
        content: Template source passed to publish_template.
        error_type: Exception class publishing is expected to raise.
    """
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(error_type),
    ):
        publish_template(name, content)

    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


@pytest.mark.parametrize(
    "content",
    [
        "{% if enabled %}<view />",
        "<view>{{ value|missing_hyperview_filter }}</view>",
        "{% unknown_hyperview_tag %}<view />",
    ],
)
def test_publication_rejects_invalid_django_template_syntax_before_writing(
    publication_model: type[Model], content: str
) -> None:
    """Database publication compiles Django syntax without rendering context.

    Args:
        publication_model: Template model class whose table exists on every alias.
        content: Template source passed to publish_template.
    """
    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(ValidationError) as captured,
    ):
        publish_template("screen.xml", content)

    assert captured.value.error_dict["content"][0].code == "django_syntax"
    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


def test_publication_reports_excessive_nesting_as_a_validation_error(
    publication_model: type[Model],
) -> None:
    """Django's recursive parser gives up on deeply nested blocks.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
    content = "{% if x %}" * 3000 + "{% endif %}" * 3000

    with (
        patch(SCHEDULE) as schedule,
        pytest.raises(ValidationError) as captured,
    ):
        publish_template("screen.xml", content)

    assert captured.value.error_dict["content"][0].code == "too_deep"
    assert publication_model.objects.count() == 0
    schedule.assert_not_called()


def test_invalid_django_syntax_does_not_increment_an_existing_revision(
    publication_model: type[Model],
) -> None:
    """A rejected update preserves the last published source and revision.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
    template = publication_model.objects.create(name="screen.xml", content="<view />")

    with pytest.raises(ValidationError):
        publish_template("screen.xml", "{% for item in items %}<view />")

    template.refresh_from_db()
    assert (template.content, template.revision) == ("<view />", 1)


@pytest.mark.parametrize("expected", [True, 0, -1, "1"])
def test_invalid_expected_revision_fails_before_database_access(
    expected: object,
) -> None:
    """An invalid expected revision fails before any database access.

    Args:
        expected: Expected revision passed to publish_template.
    """
    with (
        patch("dj_hyperview.contrib.database.services.transaction.atomic") as atomic,
        pytest.raises(ValueError, match="expected_revision"),
    ):
        publish_template("screen.xml", "<view />", expected_revision=expected)

    atomic.assert_not_called()


def test_publication_validates_then_saves_once_under_row_lock(
    publication_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing locks the row, validates once and saves once.

    Args:
        publication_model: Template model class whose table exists on every alias.
        monkeypatch: Wraps the row lock, validation and save to record their order.
    """
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
    assert events == ["lock", "clean", "save", "lock"]
    assert publication_model.objects.get(pk=template.pk).content == "<new />"


def test_publish_selects_write_router_once_and_honors_explicit_alias(
    publication_model: type[Model],
) -> None:
    """Publishing asks the write router once and honors an explicit alias.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
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
    """An invalid database alias fails with a redacted error.

    Args:
        using: Database alias passed to publish_template.
    """
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
    """A lost creation race becomes a redacted publication conflict.

    Args:
        publication_model: Template model class whose table exists on every alias.
        monkeypatch: Makes the first lookup miss an existing row.
    """
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
    """An unexplained integrity error on create stays a database error.

    Args:
        publication_model: Template model class whose table exists on every alias.
        monkeypatch: Makes saving the model raise an integrity error.
    """
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
    """An integrity error on update stays a database error.

    Args:
        publication_model: Template model class whose table exists on every alias.
        monkeypatch: Makes saving the model raise an integrity error.
    """
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
    """A SQLite constraint error on update is not reported as a publication conflict.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
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
    """A database failure or an outer rollback leaves no publication behind.

    Args:
        publication_model: Template model class whose table exists on every alias.
        monkeypatch: Makes saving the model raise a database error.

    Raises:
        RuntimeError: Raised on purpose inside the transaction to force a rollback.
    """

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
    """A cache failure after the commit keeps the published row.

    Args:
        publication_model: Template model class whose table exists on every alias.
    """
    with (
        patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates",
            side_effect=SourceUnavailable("cache:test", "backend failure"),
        ),
        pytest.raises(SourceUnavailable, match="backend failure"),
    ):
        publish_template("screen.xml", "<committed />")

    assert publication_model.objects.get(name="screen.xml").content == "<committed />"
