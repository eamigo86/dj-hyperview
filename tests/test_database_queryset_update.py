"""Controlled database QuerySet update integration tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import Case, F, Model, QuerySet, Value, When
from django.db.models.signals import post_save, pre_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

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


def test_update_mutates_only_primary_keys_captured_by_the_locked_snapshot(
    queryset_model: type[Model], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = queryset_model.objects.create(name="original.xml", content="<old />")
    django_update = QuerySet.update
    inserted = False

    def insert_phantom_then_update(queryset: QuerySet, **kwargs: Any) -> int:
        nonlocal inserted
        if not inserted:
            inserted = True
            queryset_model._base_manager.using(queryset.db).bulk_create(
                [queryset_model(name="phantom.xml", content="<old />")]
            )
        return django_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", insert_phantom_then_update)
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
