"""Database model signal integration tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.db import connection, connections, transaction
from django.db.models import Model, QuerySet
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from dj_hyperview.contrib.database import signals as database_signals
from dj_hyperview.contrib.database.apps import DjHyperviewDatabaseConfig
from dj_hyperview.contrib.database.signals import _connect_signal_handlers
from dj_hyperview.exceptions import InvalidTemplateName, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from tests.test_database_app import run_isolated

ALIASES = ("default", "replica")
LOCMEM_CACHES = {
    "signals": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "database-signal-edge-cases",
    }
}


def database_config(namespace: str) -> dict[str, object]:
    """Return database-source settings backed by an isolated cache namespace.

    Args:
        namespace: Cache namespace for one regression scenario.

    Returns:
        Hyperview settings for the explicit default database.
    """
    return {
        "SOURCES": [
            {
                "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
                "OPTIONS": {"using": "default"},
            }
        ],
        "CACHE": {"ALIAS": "signals", "NAMESPACE": namespace},
    }


def test_app_ready_connects_signal_handlers() -> None:
    with patch(
        "dj_hyperview.contrib.database.signals._connect_signal_handlers"
    ) as connect:
        DjHyperviewDatabaseConfig.create("dj_hyperview.contrib.database").ready()

    connect.assert_called_once_with()


@pytest.fixture
def signal_model(transactional_db: None) -> Iterator[type[Model]]:
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    _connect_signal_handlers()
    with connection.schema_editor() as editor:
        editor.create_model(HyperviewTemplate)
    yield HyperviewTemplate
    with connection.schema_editor() as editor:
        editor.delete_model(HyperviewTemplate)


@pytest.fixture
def dual_signal_model(django_db_blocker: Any) -> Iterator[type[Model]]:
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


def test_irrelevant_save_skips_but_raw_save_schedules(
    signal_model: type[Model],
) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.save(update_fields={"updated_at"})
        schedule.assert_not_called()
        template.content = "<raw />"
        template.save_base(raw=True, using="default", update_fields={"content"})

    schedule.assert_called_once_with("screen.xml", using="default")


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

    assert len(queries) == 0
    schedule.assert_not_called()
    assert signal_model.objects.get(pk=template.pk).name == "safe.xml"


def test_invalid_legacy_name_can_be_repaired(signal_model: type[Model]) -> None:
    """A canonical replacement can recover a row created outside normal writes."""
    signal_model._base_manager.bulk_create(
        [signal_model(name=r"bad\name.xml", content="<view />")]
    )
    template = signal_model._base_manager.get()

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.name = "repaired.xml"
        template.save(update_fields={"name"})

    assert signal_model.objects.get(pk=template.pk).name == "repaired.xml"
    schedule.assert_called_once_with("repaired.xml", using="default")


def test_invalid_legacy_name_can_be_deleted(signal_model: type[Model]) -> None:
    """An uncanonical persisted name never makes its row immortal."""
    signal_model._base_manager.bulk_create(
        [signal_model(name=r"bad\name.xml", content="<view />")]
    )
    template = signal_model._base_manager.get()

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.delete()

    assert signal_model.objects.count() == 0
    schedule.assert_not_called()


def test_instance_delete_schedules_its_name(signal_model: type[Model]) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.delete()

    schedule.assert_called_once_with("screen.xml", using="default")


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_direct_mutations_lock_the_persisted_name_inside_a_transaction(
    signal_model: type[Model], operation: str
) -> None:
    """Direct instance writes cannot race an unlocked persisted-name snapshot."""
    template = signal_model.objects.create(name="screen.xml", content="<view />")
    original = QuerySet.select_for_update
    lock_states: list[bool] = []

    def observe_lock(queryset: QuerySet, *args: object, **kwargs: object) -> QuerySet:
        lock_states.append(connections[queryset.db].in_atomic_block)
        return original(queryset, *args, **kwargs)

    with patch.object(QuerySet, "select_for_update", observe_lock):
        if operation == "save":
            template.content = "<updated />"
            template.save(update_fields={"content"})
        else:
            template.delete()

    assert lock_states == [True]


def test_queryset_delete_uses_one_snapshot_and_one_invalidation(
    signal_model: type[Model],
) -> None:
    """Bulk deletion avoids per-row lookups and post-commit callbacks."""
    signal_model.objects.bulk_create(
        [
            signal_model(name=f"screen-{index}.xml", content="<view />")
            for index in range(3)
        ]
    )

    with (
        patch.object(
            database_signals,
            "_persisted_name",
            wraps=database_signals._persisted_name,
        ) as persisted_name,
        patch.object(database_signals, "_schedule_invalidation") as per_row,
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as batch,
    ):
        deleted, _ = signal_model.objects.all().delete()

    assert deleted == 3
    persisted_name.assert_not_called()
    per_row.assert_not_called()
    batch.assert_called_once_with(
        "screen-0.xml", "screen-1.xml", "screen-2.xml", using="default"
    )


def test_already_deleted_instance_preserves_fallback_invalidation(
    signal_model: type[Model],
) -> None:
    template = signal_model.objects.create(name="screen.xml", content="<view />")
    signal_model.objects.filter(pk=template.pk).delete()

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.delete()

    schedule.assert_called_once_with("screen.xml", using="default")


@override_settings(
    CACHES=LOCMEM_CACHES,
    HYPERVIEW=database_config("delete-persisted-name"),
)
def test_instance_delete_invalidates_persisted_name_after_unsaved_rename(
    signal_model: type[Model],
) -> None:
    caches["signals"].clear()

    with (
        patch("dj_hyperview.checks.apps.is_installed", return_value=True),
        patch(
            "dj_hyperview.contrib.database.sources._template_model",
            return_value=signal_model,
        ),
    ):
        template = signal_model.objects.create(
            name="persisted.xml", content="<persisted />"
        )
        assert TemplateResolver.from_settings().resolve("persisted.xml").content == (
            "<persisted />"
        )
        template.name = "unsaved.xml"
        template.delete()

        assert not signal_model.objects.filter(name="persisted.xml").exists()
        with pytest.raises(TemplateNotFound):
            TemplateResolver.from_settings().resolve("persisted.xml")


@pytest.mark.parametrize("force_update", [False, True])
@override_settings(
    CACHES=LOCMEM_CACHES,
    HYPERVIEW=database_config("detached-persisted-name"),
)
def test_detached_update_invalidates_persisted_name(
    signal_model: type[Model], force_update: bool
) -> None:
    caches["signals"].clear()

    with (
        patch("dj_hyperview.checks.apps.is_installed", return_value=True),
        patch(
            "dj_hyperview.contrib.database.sources._template_model",
            return_value=signal_model,
        ),
    ):
        stored = signal_model.objects.create(name="old.xml", content="<old />")
        assert TemplateResolver.from_settings().resolve("old.xml").content == "<old />"
        detached = signal_model(
            id=stored.pk,
            name="new.xml",
            content="<new />",
            active=True,
            revision=2,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )
        detached.save(force_update=force_update)

        assert signal_model.objects.get(pk=stored.pk).name == "new.xml"
        with pytest.raises(TemplateNotFound):
            TemplateResolver.from_settings().resolve("old.xml")
        assert TemplateResolver.from_settings().resolve("new.xml").content == "<new />"


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
def test_replica_detached_update_reads_persisted_name_from_mutation_alias(
    dual_signal_model: type[Model],
) -> None:
    default = dual_signal_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_signal_model.objects.using("replica").create(
        id=default.pk, name="replica-old.xml", content="<replica />"
    )
    detached = dual_signal_model(
        id=replica.pk,
        name="replica-new.xml",
        content="<new />",
        created_at=replica.created_at,
        updated_at=replica.updated_at,
    )

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        detached.save(force_update=True, using="replica")

    schedule.assert_called_once_with(
        "replica-old.xml", "replica-new.xml", using="replica"
    )


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_replica_delete_ignores_unsaved_name_and_uses_mutation_alias(
    dual_signal_model: type[Model],
) -> None:
    default = dual_signal_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_signal_model.objects.using("replica").create(
        id=default.pk, name="replica.xml", content="<replica />"
    )
    replica.name = "../unsaved.xml"

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        replica.delete(using="replica")

    schedule.assert_called_once_with("replica.xml", using="replica")


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

    invalidate.assert_called_once_with("first.xml", "second.xml")


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
