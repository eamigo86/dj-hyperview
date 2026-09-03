"""Database model signal integration tests."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from django.db import connection, connections, transaction
from django.db.models import Model
from django.test.utils import CaptureQueriesContext

from dj_hyperview.contrib.database.apps import DjHyperviewDatabaseConfig
from dj_hyperview.contrib.database.signals import _connect_signal_handlers
from dj_hyperview.exceptions import InvalidTemplateName
from tests.test_database_app import run_isolated

ALIASES = ("default", "replica")


def test_app_ready_connects_signal_handlers() -> None:
    with patch(
        "dj_hyperview.contrib.database.signals._connect_signal_handlers"
    ) as connect:
        DjHyperviewDatabaseConfig.create("dj_hyperview.contrib.database").ready()

    connect.assert_called_once_with()


@pytest.fixture
def signal_model(transactional_db) -> Iterator[type[Model]]:
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    _connect_signal_handlers()
    with connection.schema_editor() as editor:
        editor.create_model(HyperviewTemplate)
    yield HyperviewTemplate
    with connection.schema_editor() as editor:
        editor.delete_model(HyperviewTemplate)


@pytest.fixture
def dual_signal_model(django_db_blocker) -> Iterator[type[Model]]:
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    _connect_signal_handlers()
    with django_db_blocker.unblock():
        for alias in ALIASES:
            with connections[alias].schema_editor() as editor:
                editor.create_model(HyperviewTemplate)
    yield HyperviewTemplate
    with django_db_blocker.unblock():
        for alias in reversed(ALIASES):
            with connections[alias].schema_editor() as editor:
                editor.delete_model(HyperviewTemplate)


def test_created_template_schedules_invalidation(signal_model: type[Model]) -> None:
    _connect_signal_handlers()
    _connect_signal_handlers()
    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        signal_model.objects.create(name="screen.xml", content="<view />")

    schedule.assert_called_once_with("screen.xml", using="default")


def test_renamed_template_schedules_old_and_new_names(
    signal_model: type[Model],
) -> None:
    template = signal_model.objects.create(name="old.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.name = "new.xml"
        template.save()

    schedule.assert_called_once_with("old.xml", "new.xml", using="default")


@pytest.mark.parametrize(
    ("field", "value"),
    [("content", "<new />"), ("active", False), ("revision", 2)],
)
def test_observable_field_update_schedules_current_name(
    signal_model: type[Model], field: str, value: object
) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        setattr(template, field, value)
        template.save(update_fields={field})

    schedule.assert_called_once_with("screen.xml", using="default")


def test_irrelevant_and_raw_saves_do_not_schedule(signal_model: type[Model]) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.save(update_fields={"updated_at"})
        template.content = "<raw />"
        template.save_base(raw=True, using="default", update_fields={"content"})

    schedule.assert_not_called()


def test_unsafe_new_name_fails_before_database_access(
    signal_model: type[Model],
) -> None:
    template = signal_model(name="../private.xml", content="<view />")

    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(InvalidTemplateName, match="Invalid template name"):
            template.save()

    assert len(queries) == 0
    assert signal_model.objects.count() == 0


def test_unsafe_rename_fails_before_update_sql(signal_model: type[Model]) -> None:
    template = signal_model.objects.create(name="safe.xml", content="<view />")
    template.name = "../private.xml"

    with (
        patch(
            "dj_hyperview.contrib.database.signals._schedule_invalidation"
        ) as schedule,
        CaptureQueriesContext(connection) as queries,
    ):
        with pytest.raises(InvalidTemplateName, match="Invalid template name"):
            template.save(update_fields={"name"})

    assert len(queries) == 1
    assert "UPDATE" not in queries[0]["sql"].upper()
    schedule.assert_not_called()
    assert signal_model.objects.get(pk=template.pk).name == "safe.xml"


def test_instance_delete_schedules_its_name(signal_model: type[Model]) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.delete()

    schedule.assert_called_once_with("screen.xml", using="default")


def test_save_callbacks_follow_commit_and_rollback(signal_model: type[Model]) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic(using="default"):
            template.content = "<committed />"
            template.save(update_fields={"content"})
            invalidate.assert_not_called()
        invalidate.assert_called_once_with("screen.xml")
        invalidate.reset_mock()

        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic(using="default"):
                template.content = "<rolled-back />"
                template.save(update_fields={"content"})
                raise RuntimeError("rollback")

    invalidate.assert_not_called()


def test_nested_savepoint_rollback_discards_signal_callback(
    signal_model: type[Model],
) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic(using="default"):
            with pytest.raises(RuntimeError, match="savepoint"):
                with transaction.atomic(using="default"):
                    template.content = "<nested />"
                    template.save(update_fields={"content"})
                    raise RuntimeError("savepoint")
            invalidate.assert_not_called()

    invalidate.assert_not_called()


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_replica_rename_reads_old_name_from_mutation_alias(
    dual_signal_model: type[Model],
) -> None:
    default = dual_signal_model.objects.using("default").create(
        name="default.xml", content="<view />"
    )
    replica = dual_signal_model.objects.using("replica").create(
        id=default.pk, name="replica-old.xml", content="<view />"
    )

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        replica.name = "replica-new.xml"
        replica.save(using="replica")

    schedule.assert_called_once_with(
        "replica-old.xml", "replica-new.xml", using="replica"
    )


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_queryset_delete_callbacks_follow_replica_commit_and_rollback(
    dual_signal_model: type[Model],
) -> None:
    for name in ("first.xml", "second.xml"):
        dual_signal_model.objects.using("replica").create(name=name, content="<view />")

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic(using="replica"):
            dual_signal_model.objects.using("replica").filter(name="first.xml").delete()
            transaction.set_rollback(True, using="replica")
        invalidate.assert_not_called()

        with transaction.atomic(using="replica"):
            dual_signal_model.objects.using("replica").all().delete()
            invalidate.assert_not_called()

    assert invalidate.call_count == 2
    assert {item.args for item in invalidate.call_args_list} == {
        ("first.xml",),
        ("second.xml",),
    }


@pytest.mark.parametrize(
    ("settings_module", "expected"),
    [("tests.settings", "False"), ("tests.settings_database", "True")],
)
def test_optional_app_ready_imports_signals_without_queries(
    settings_module: str, expected: str
) -> None:
    source = (
        "import django,sys; from unittest.mock import patch; "
        "from django.db.backends.utils import CursorWrapper; "
        "p=patch.object(CursorWrapper,'execute',side_effect=AssertionError('SQL')); "
        "p.start(); django.setup(); p.stop(); "
        "print('dj_hyperview.contrib.database.signals' in sys.modules)"
    )

    result = run_isolated(settings_module, source)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
