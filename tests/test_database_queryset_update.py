"""Controlled database QuerySet update integration tests."""

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import django
import pytest
from django.core.cache import caches
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import (
    IntegrityError,
    NotSupportedError,
    connection,
    connections,
    transaction,
)
from django.db.models import Case, Count, F, Model, QuerySet, Value, When
from django.db.models.functions import Concat
from django.db.models.signals import post_save, pre_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from dj_hyperview.contrib.database import querysets as database_querysets
from dj_hyperview.contrib.database.querysets import HyperviewTemplateQuerySet
from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.resolver import TemplateResolver

ALIASES = ("default", "replica")
CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "queryset-update",
    }
}
HYPERVIEW = {
    "SOURCES": [
        {
            "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
            "OPTIONS": {"using": "default"},
        }
    ],
    "CACHE": {"ALIAS": "screens", "NAMESPACE": "queryset-update"},
}


class _TrackedValue(Value):
    def __init__(
        self,
        value: str,
        events: list[str],
        *,
        fail_after: int | None = None,
    ) -> None:
        super().__init__(value)
        self.events = events
        self.fail_after = fail_after

    def resolve_expression(self, *args: object, **kwargs: object) -> Any:
        self.events.append("resolve")
        if self.fail_after is not None and len(self.events) > self.fail_after:
            raise RuntimeError("expression resolved more than native update")
        return super().resolve_expression(*args, **kwargs)


class _SplitReadWriteRouter:
    def db_for_read(self, model: type[Model], **hints: Any) -> str:
        return "default"

    def db_for_write(self, model: type[Model], **hints: Any) -> str:
        return "replica"


@pytest.fixture
def queryset_model(transactional_db: None) -> Iterator[type[Model]]:
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    with connection.schema_editor() as editor:
        editor.create_model(HyperviewTemplate)
    yield HyperviewTemplate
    with connection.schema_editor() as editor:
        editor.delete_model(HyperviewTemplate)


@pytest.fixture
def dual_queryset_model(django_db_blocker: Any) -> Iterator[type[Model]]:
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    with django_db_blocker.unblock():
        for alias in ALIASES:
            with connections[alias].schema_editor() as editor:
                editor.create_model(HyperviewTemplate)
    yield HyperviewTemplate
    with django_db_blocker.unblock():
        for alias in reversed(ALIASES):
            with connections[alias].schema_editor() as editor:
                editor.delete_model(HyperviewTemplate)


def test_content_update_schedules_names_and_preserves_result(
    queryset_model: type[Model],
) -> None:
    queryset_model.objects.create(name="first.xml", content="<first />")
    queryset_model.objects.create(name="second.xml", content="<second />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = queryset_model.objects.filter(active=True).update(
            content="<updated />"
        )

    assert updated == 2
    assert isinstance(queryset_model.objects.all(), HyperviewTemplateQuerySet)
    assert list(
        queryset_model.objects.order_by("name").values_list("content", flat=True)
    ) == ["<updated />", "<updated />"]
    schedule.assert_called_once_with("first.xml", "second.xml", using="default")


def test_update_chunks_primary_keys_to_the_backend_parameter_limit(
    queryset_model: type[Model],
) -> None:
    """Update batches reserve placeholders for assigned values."""
    queryset_model.objects.bulk_create(
        [
            queryset_model(name=f"screen-{index}.xml", content="<view />")
            for index in range(7)
        ]
    )
    parameter_counts: list[int] = []

    def enforce_parameter_limit(
        execute: Callable[..., Any],
        sql: str,
        params: tuple[object, ...] | None,
        many: bool,
        context: dict[str, object],
    ) -> Any:
        if sql.lstrip().upper().startswith("UPDATE"):
            parameter_counts.append(len(params or ()))
            assert parameter_counts[-1] <= 7
        return execute(sql, params, many, context)

    with (
        patch.object(connection.ops, "bulk_batch_size", return_value=7),
        connection.execute_wrapper(enforce_parameter_limit),
    ):
        updated = queryset_model.objects.update(active=False)

    assert updated == 7
    assert parameter_counts == [7, 2]
    assert queryset_model.objects.filter(active=False).count() == 7


def test_rename_identity_backfill_respects_backend_parameter_limit(
    queryset_model: type[Model],
) -> None:
    """Identity backfill accounts for CASE and primary-key placeholders."""
    queryset_model.objects.bulk_create(
        [
            queryset_model(name=f"screen-{index}.xml", content="<view />")
            for index in range(4)
        ]
    )
    identity_parameter_counts: list[int] = []

    def enforce_parameter_limit(
        execute: Callable[..., Any],
        sql: str,
        params: tuple[object, ...] | None,
        many: bool,
        context: dict[str, object],
    ) -> Any:
        if sql.lstrip().upper().startswith("UPDATE"):
            parameter_count = len(params or ())
            assert parameter_count <= 10
            if '"name_identity"' in sql:
                identity_parameter_counts.append(parameter_count)
        return execute(sql, params, many, context)

    with (
        patch.object(connection.ops, "bulk_batch_size", return_value=10),
        connection.execute_wrapper(enforce_parameter_limit),
    ):
        updated = queryset_model.objects.update(
            name=Concat(Value("renamed-"), F("name"))
        )

    assert updated == 4
    assert identity_parameter_counts == [9, 3]
    assert list(queryset_model.objects.values_list("name", flat=True)) == [
        f"renamed-screen-{index}.xml" for index in range(4)
    ]


@override_settings(CACHES=CACHES, HYPERVIEW=HYPERVIEW)
def test_content_update_makes_cached_result_observe_committed_content(
    queryset_model: type[Model],
) -> None:
    caches["screens"].clear()
    with (
        patch("dj_hyperview.checks.apps.is_installed", return_value=True),
        patch(
            "dj_hyperview.contrib.database.sources._template_model",
            return_value=queryset_model,
        ),
    ):
        template = queryset_model.objects.create(name="screen.xml", content="<old />")
        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "<old />"
        )

        queryset_model.objects.filter(pk=template.pk).update(content="<new />")

        assert (
            TemplateResolver.from_settings().resolve("screen.xml").content == "<new />"
        )


def test_bulk_create_rejects_noncanonical_names_before_database_access(
    queryset_model: type[Model],
) -> None:
    """Bulk insertion cannot create new inert or unsafe template rows."""
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(InvalidTemplateName, match="Invalid template name"):
            queryset_model.objects.bulk_create(
                [queryset_model(name="../private.xml", content="<view />")]
            )

    assert len(queries) == 0
    assert queryset_model.objects.count() == 0


@pytest.mark.parametrize("unique_fields", [["pk"], ["name_identity"], None])
def test_bulk_upsert_is_rejected_before_consuming_objects_or_database_access(
    queryset_model: type[Model], unique_fields: list[str] | None
) -> None:
    """Unsupported upserts must have no iterable, SQL, or cache side effects."""
    consumed: list[bool] = []

    def objects() -> Iterator[Model]:
        consumed.append(True)
        yield queryset_model(name="screen.xml", content="<new />")

    with (
        CaptureQueriesContext(connection) as queries,
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        pytest.raises(NotSupportedError, match="publication services"),
    ):
        queryset_model.objects.bulk_create(
            objects(),
            update_conflicts=True,
            update_fields=["content"],
            unique_fields=unique_fields,
        )

    assert consumed == []
    assert len(queries) == 0
    schedule.assert_not_called()


def test_bulk_insert_with_ignored_conflict_preserves_the_existing_row(
    queryset_model: type[Model],
) -> None:
    """Imports may ignore duplicate names without silently publishing updates."""
    existing = queryset_model.objects.create(name="screen.xml", content="<old />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        queryset_model.objects.bulk_create(
            [
                queryset_model(name="screen.xml", content="<new />"),
                queryset_model(name="other.xml", content="<other />"),
            ],
            ignore_conflicts=True,
        )

    existing.refresh_from_db()
    assert existing.content == "<old />"
    assert queryset_model.objects.count() == 2
    schedule.assert_called_once_with("screen.xml", "other.xml", using="default")


def test_rename_schedules_old_and_actual_new_names(queryset_model: type[Model]) -> None:
    first = queryset_model.objects.create(name="first.xml", content="<first />")
    second = queryset_model.objects.create(name="second.xml", content="<second />")

    rename = Case(
        When(pk=first.pk, then=Value("renamed.xml")),
        default=F("name"),
    )
    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = queryset_model.objects.update(name=rename)

    assert updated == 2
    assert list(
        queryset_model.objects.order_by("pk").values_list("name", flat=True)
    ) == [
        "renamed.xml",
        second.name,
    ]
    schedule.assert_called_once_with(
        "first.xml", "second.xml", "renamed.xml", using="default"
    )


def test_literal_rename_updates_name_and_identity_in_one_statement(
    queryset_model: type[Model],
) -> None:
    """A constant rename needs no read-back or CASE identity update."""
    template = queryset_model.objects.create(name="old.xml", content="<view />")

    with (
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        CaptureQueriesContext(connection) as queries,
    ):
        updated = queryset_model.objects.filter(pk=template.pk).update(
            name="renamed.xml"
        )

    update_sql = [
        query["sql"]
        for query in queries
        if query["sql"].lstrip().upper().startswith("UPDATE")
    ]
    assert updated == 1
    assert len(update_sql) == 1
    assert '"name"' in update_sql[0]
    assert '"name_identity"' in update_sql[0]
    template.refresh_from_db()
    assert template.name == "renamed.xml"
    assert template.name_identity == database_querysets.template_name_identity(
        "renamed.xml"
    )
    schedule.assert_called_once_with("old.xml", "renamed.xml", using="default")


def test_filtered_zero_and_noop_updates_preserve_queryset_semantics(
    queryset_model: type[Model],
) -> None:
    queryset_model.objects.create(name="first.xml", content="<first />")
    queryset_model.objects.create(name="second.xml", content="<second />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        assert queryset_model.objects.filter(name="first.xml").update(active=False) == 1
        schedule.assert_called_once_with("first.xml", using="default")
        schedule.reset_mock()
        assert (
            queryset_model.objects.filter(name="missing.xml").update(active=False) == 0
        )
        assert queryset_model.objects.none().update() == 0

    assert queryset_model.objects.get(name="first.xml").active is False
    assert queryset_model.objects.get(name="second.xml").active is True
    schedule.assert_not_called()


def test_queryset_updates_can_disable_and_repair_invalid_legacy_names(
    queryset_model: type[Model],
) -> None:
    """Old invalid names are skipped while replacement names stay strict."""
    queryset_model._base_manager.bulk_create(
        [queryset_model(name=r"bad\name.xml", content="<view />")]
    )
    template = queryset_model._base_manager.get()

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        assert queryset_model.objects.filter(pk=template.pk).update(active=False) == 1
        schedule.assert_not_called()
        assert (
            queryset_model.objects.filter(pk=template.pk).update(name="repaired.xml")
            == 1
        )

    repaired = queryset_model.objects.get(pk=template.pk)
    assert (repaired.name, repaired.active) == ("repaired.xml", False)
    schedule.assert_called_once_with("repaired.xml", using="default")


def test_updates_follow_outer_commit_rollback_and_savepoint(
    queryset_model: type[Model],
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic():
            queryset_model.objects.filter(pk=template.pk).update(content="<commit />")
            invalidate.assert_not_called()
        invalidate.assert_called_once_with("screen.xml")
        invalidate.reset_mock()

        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                queryset_model.objects.filter(pk=template.pk).update(active=False)
                raise RuntimeError("rollback")
        with transaction.atomic():
            with pytest.raises(RuntimeError, match="savepoint"):
                with transaction.atomic():
                    queryset_model.objects.filter(pk=template.pk).update(active=False)
                    raise RuntimeError("savepoint")

    invalidate.assert_not_called()
    template.refresh_from_db()
    assert (template.content, template.active) == ("<commit />", True)


def test_invalid_literal_and_expression_names_roll_back_without_scheduling(
    queryset_model: type[Model],
) -> None:
    template = queryset_model.objects.create(name="safe.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        with CaptureQueriesContext(connection) as literal_queries:
            with pytest.raises(InvalidTemplateName):
                queryset_model.objects.update(name="../literal.xml")
        with pytest.raises(InvalidTemplateName):
            queryset_model.objects.update(name=Value("../expression.xml"))

    assert not any("UPDATE" in query["sql"].upper() for query in literal_queries)
    assert queryset_model.objects.get(pk=template.pk).name == "safe.xml"
    schedule.assert_not_called()


def test_failed_sql_and_primary_key_updates_have_no_partial_effect(
    queryset_model: type[Model],
) -> None:
    queryset_model.objects.create(name="first.xml", content="<first />")
    queryset_model.objects.create(name="second.xml", content="<second />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        with pytest.raises(IntegrityError):
            queryset_model.objects.update(name="duplicate.xml")
        with CaptureQueriesContext(connection) as primary_key_queries:
            with pytest.raises(ValueError, match="primary key"):
                queryset_model.objects.update(pk=999)
        with pytest.raises(FieldDoesNotExist):
            queryset_model.objects.update(unknown="value")

    assert list(
        queryset_model.objects.order_by("name").values_list("name", flat=True)
    ) == [
        "first.xml",
        "second.xml",
    ]
    assert len(primary_key_queries) == 0
    schedule.assert_not_called()


def test_queryset_update_requests_row_lock_and_does_not_emit_model_signals(
    queryset_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")
    selected: list[str] = []
    original = HyperviewTemplateQuerySet.select_for_update

    def select_for_update(
        queryset: HyperviewTemplateQuerySet,
    ) -> HyperviewTemplateQuerySet:
        selected.append(queryset.db)
        return original(queryset)

    monkeypatch.setattr(
        HyperviewTemplateQuerySet, "select_for_update", select_for_update
    )
    with (
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        patch.object(pre_save, "send", wraps=pre_save.send) as pre_save_send,
        patch.object(post_save, "send", wraps=post_save.send) as post_save_send,
    ):
        queryset_model.objects.filter(pk=template.pk).update(content=F("content"))

    assert selected == ["default"]
    schedule.assert_called_once_with("screen.xml", using="default")
    pre_save_send.assert_not_called()
    post_save_send.assert_not_called()


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_update_uses_the_selected_database_alias(
    dual_queryset_model: type[Model],
) -> None:
    default = dual_queryset_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_queryset_model.objects.using("replica").create(
        id=default.pk, name="replica.xml", content="<replica />"
    )

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = (
            dual_queryset_model.objects.using("replica")
            .filter(pk=replica.pk)
            .update(content="<updated />")
        )

    assert updated == 1
    assert dual_queryset_model.objects.using("default").get(pk=default.pk).content == (
        "<default />"
    )
    assert dual_queryset_model.objects.using("replica").get(pk=replica.pk).content == (
        "<updated />"
    )
    schedule.assert_called_once_with("replica.xml", using="replica")


@override_settings(DATABASE_ROUTERS=[_SplitReadWriteRouter()])
@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_implicit_update_resolves_write_router_before_snapshot(
    dual_queryset_model: type[Model],
) -> None:
    default = dual_queryset_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_queryset_model.objects.using("replica").create(
        id=default.pk, name="replica.xml", content="<replica />"
    )

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = dual_queryset_model.objects.filter(pk=replica.pk).update(
            content="<written />"
        )

    assert updated == 1
    assert dual_queryset_model.objects.using("default").get(pk=default.pk).content == (
        "<default />"
    )
    assert dual_queryset_model.objects.using("replica").get(pk=replica.pk).content == (
        "<written />"
    )
    schedule.assert_called_once_with("replica.xml", using="replica")


@override_settings(DATABASE_ROUTERS=[_SplitReadWriteRouter()])
@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_implicit_delete_resolves_write_router_before_snapshot(
    dual_queryset_model: type[Model],
) -> None:
    """Implicit deletes use the write database for snapshot and mutation."""
    default = dual_queryset_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_queryset_model.objects.using("replica").create(
        id=default.pk, name="replica.xml", content="<replica />"
    )

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        deleted, _ = dual_queryset_model.objects.filter(pk=replica.pk).delete()

    assert deleted == 1
    assert dual_queryset_model.objects.using("default").filter(pk=default.pk).exists()
    assert not (
        dual_queryset_model.objects.using("replica").filter(pk=replica.pk).exists()
    )
    schedule.assert_called_once_with("replica.xml", using="replica")


def test_update_mutates_only_primary_keys_captured_by_the_locked_snapshot(
    queryset_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = queryset_model.objects.create(name="original.xml", content="<old />")
    execute_update = database_querysets._execute_update
    inserted = False

    def insert_phantom_then_update(query: Any, using: str) -> int:
        nonlocal inserted
        if not inserted:
            inserted = True
            queryset_model._base_manager.using(using).bulk_create(
                [queryset_model(name="phantom.xml", content="<old />")]
            )
        return execute_update(query, using)

    monkeypatch.setattr(
        database_querysets, "_execute_update", insert_phantom_then_update
    )
    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = queryset_model.objects.filter(active=True).update(
            content="<updated />"
        )

    assert updated == 1
    assert queryset_model.objects.get(pk=original.pk).content == "<updated />"
    assert queryset_model.objects.get(name="phantom.xml").content == "<old />"
    schedule.assert_called_once_with("original.xml", using="default")


def test_update_preserves_aliases_used_by_expressions(
    queryset_model: type[Model],
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = (
            queryset_model.objects.alias(replacement=Value("<aliased />"))
            .filter(pk=template.pk)
            .update(content=F("replacement"))
        )

    assert updated == 1
    assert queryset_model.objects.get(pk=template.pk).content == "<aliased />"
    schedule.assert_called_once_with("screen.xml", using="default")


def test_union_update_preserves_native_rejection_without_writing(
    queryset_model: type[Model],
) -> None:
    first = queryset_model.objects.create(name="first.xml", content="<first />")
    second = queryset_model.objects.create(name="second.xml", content="<second />")
    combined = (
        queryset_model.objects.filter(pk=first.pk)
        .order_by()
        .union(queryset_model.objects.filter(pk=second.pk).order_by())
    )

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        with pytest.raises(NotSupportedError):
            combined.update(content="<unexpected />")

    assert list(
        queryset_model.objects.order_by("pk").values_list("content", flat=True)
    ) == ["<first />", "<second />"]
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("build_queryset", "message"),
    [
        pytest.param(
            lambda model: model.objects.all()[:1],
            "Cannot update a query once a slice has been taken.",
            id="sliced",
        ),
    ],
)
def test_update_preserves_native_shape_preconditions_before_queries(
    queryset_model: type[Model],
    build_queryset: Callable[[type[Model]], QuerySet],
    message: str,
) -> None:
    queryset_model.objects.create(name="screen.xml", content="<old />")

    with (
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        CaptureQueriesContext(connection) as queries,
    ):
        with pytest.raises(TypeError) as error:
            build_queryset(queryset_model).update(content="<unexpected />")

    assert len(queries) == 0
    assert str(error.value) == message
    assert queryset_model.objects.get().content == "<old />"
    schedule.assert_not_called()


def test_update_preserves_annotation_ordering_and_rejects_aggregate_ordering(
    queryset_model: type[Model],
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        updated = (
            queryset_model.objects.annotate(replacement=Value("<annotated />"))
            .order_by("-replacement")
            .update(content=F("replacement"))
        )
        assert updated == 1
        schedule.assert_called_once_with("screen.xml", using="default")
        schedule.reset_mock()
        with CaptureQueriesContext(connection) as queries:
            with pytest.raises(
                FieldError, match="Cannot update when ordering by an aggregate"
            ):
                queryset_model.objects.alias(total=Count("pk")).order_by(
                    "total"
                ).update(content="<unexpected />")

    assert len(queries) == 0
    assert queryset_model.objects.get(pk=template.pk).content == "<annotated />"
    schedule.assert_not_called()


@pytest.mark.parametrize(
    ("changes", "error_type"),
    [
        pytest.param(
            {"content": "<new />", "unknown": "value"},
            FieldDoesNotExist,
            id="unknown-field",
        ),
        pytest.param(
            {"content": F("missing_alias")},
            FieldError,
            id="unresolved-expression",
        ),
    ],
)
def test_invalid_update_values_fail_before_database_queries(
    queryset_model: type[Model],
    changes: dict[str, Any],
    error_type: type[Exception],
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")

    with (
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        CaptureQueriesContext(connection) as queries,
    ):
        with pytest.raises(error_type):
            queryset_model.objects.filter(pk=template.pk).update(**changes)

    assert len(queries) == 0
    assert queryset_model.objects.get(pk=template.pk).content == "<old />"
    schedule.assert_not_called()


def test_distinct_fields_update_matches_supported_django_version(
    queryset_model: type[Model],
) -> None:
    template = queryset_model.objects.create(name="screen.xml", content="<old />")

    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        if django.VERSION[:2] >= (6, 1):
            with CaptureQueriesContext(connection) as queries:
                with pytest.raises(
                    TypeError, match=r"Cannot call update\(\) after .distinct"
                ):
                    queryset_model.objects.distinct("name").update(content="<new />")
            assert len(queries) == 0
            schedule.assert_not_called()
        else:
            updated = queryset_model.objects.distinct("name").update(content="<new />")
            assert updated == 1
            schedule.assert_called_once_with("screen.xml", using="default")

    expected = "<old />" if django.VERSION[:2] >= (6, 1) else "<new />"
    assert queryset_model.objects.get(pk=template.pk).content == expected


def test_controlled_update_resolves_expression_only_as_native_update_does(
    queryset_model: type[Model],
) -> None:
    native = queryset_model.objects.create(name="native.xml", content="<old />")
    controlled = queryset_model.objects.create(name="controlled.xml", content="<old />")
    native_events: list[str] = []
    controlled_events: list[str] = []

    assert (
        QuerySet.update(
            queryset_model.objects.filter(pk=native.pk),
            content=_TrackedValue("<native />", native_events, fail_after=2),
        )
        == 1
    )
    with patch(
        "dj_hyperview.contrib.database.querysets._schedule_invalidation"
    ) as schedule:
        assert (
            queryset_model.objects.filter(pk=controlled.pk).update(
                content=_TrackedValue("<controlled />", controlled_events, fail_after=2)
            )
            == 1
        )

    assert native_events == ["resolve", "resolve"]
    assert controlled_events == native_events
    assert queryset_model.objects.get(pk=controlled.pk).content == "<controlled />"
    schedule.assert_called_once_with("controlled.xml", using="default")


def test_none_update_matches_native_resolution_and_query_boundary(
    queryset_model: type[Model],
) -> None:
    native_events: list[str] = []
    controlled_events: list[str] = []

    with CaptureQueriesContext(connection) as native_queries:
        assert (
            QuerySet.update(
                queryset_model.objects.none(),
                content=_TrackedValue("<native />", native_events),
            )
            == 0
        )
    with (
        patch(
            "dj_hyperview.contrib.database.querysets._schedule_invalidation"
        ) as schedule,
        CaptureQueriesContext(connection) as controlled_queries,
    ):
        assert (
            queryset_model.objects.none().update(
                content=_TrackedValue("<controlled />", controlled_events)
            )
            == 0
        )

    assert native_events == ["resolve", "resolve"]
    assert controlled_events == native_events
    assert len(native_queries) == 0
    assert len(controlled_queries) == 0
    schedule.assert_not_called()
