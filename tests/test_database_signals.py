"""Database model signal integration tests."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import call, patch

import pytest
from django.core.cache import caches
from django.db import connection, connections, transaction
from django.db.models import CharField, Manager, Model, QuerySet
from django.db.models.deletion import Collector
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext, isolate_apps

from dj_hyperview.contrib.database import signals as database_signals
from dj_hyperview.contrib.database.apps import DjHyperviewDatabaseConfig
from dj_hyperview.contrib.database.signals import _connect_signal_handlers
from dj_hyperview.exceptions import InvalidTemplateName, TemplateNotFound
from dj_hyperview.resolver import TemplateResolver
from tests.test_database_app import run_isolated

ALIASES = ("default", "replica")
SIGNALS = (pre_save, post_save, pre_delete, post_delete)
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


@pytest.fixture(params=["proxy", "proxy-of-proxy"])
def proxy_model(
    request: pytest.FixtureRequest, signal_model: type[Model]
) -> Iterator[type[Model]]:
    """Yield a proxy, or a proxy of a proxy, defined after the handlers connected.

    The classes live in an isolated registry, like a model created at runtime.
    """
    with isolate_apps():

        class ProxyTemplate(signal_model):
            class Meta:
                proxy = True
                app_label = "dj_hyperview_database"

        model = ProxyTemplate
        if request.param == "proxy-of-proxy":

            class NestedProxyTemplate(ProxyTemplate):
                class Meta:
                    proxy = True
                    app_label = "dj_hyperview_database"

            model = NestedProxyTemplate
        yield model


@pytest.fixture
def scoped_proxy_model(signal_model: type[Model]) -> Iterator[type[Model]]:
    """Yield a proxy whose default manager hides every row outside live/."""

    class LiveOnly(Manager):
        def get_queryset(self) -> QuerySet:
            return super().get_queryset().filter(name__startswith="live/")

    with isolate_apps():

        class LiveTemplate(signal_model):
            objects = LiveOnly()

            class Meta:
                proxy = True
                app_label = "dj_hyperview_database"

        yield LiveTemplate


def test_proxy_model_saves_schedule_invalidation(proxy_model: type[Model]) -> None:
    """Django sends proxy instances' signals with the proxy class as sender."""
    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template = proxy_model.objects.create(name="screen.xml", content="<view />")

    schedule.assert_called_once_with("screen.xml", using="default")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.save()

    schedule.assert_called_once_with("screen.xml", using="default")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.name = "renamed.xml"
        template.save()

    schedule.assert_called_once_with("screen.xml", "renamed.xml", using="default")


def test_proxy_model_deletes_schedule_invalidation(proxy_model: type[Model]) -> None:
    template = proxy_model.objects.create(name="screen.xml", content="<view />")

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.delete()

    schedule.assert_called_once_with("screen.xml", using="default")


def test_every_template_model_listens_to_all_four_signals(
    signal_model: type[Model], proxy_model: type[Model]
) -> None:
    for model in (signal_model, proxy_model):
        assert all(signal.has_listeners(model) for signal in SIGNALS)


def test_other_models_keep_django_fast_delete(signal_model: type[Model]) -> None:
    """Receivers belong to template classes, so no other model loses bulk delete.

    A receiver connected without a sender listens to every model, and Django only
    skips per-row deletion for a model that has no pre_delete or post_delete
    listener.
    """
    with isolate_apps():

        class Unrelated(Model):
            title = CharField(max_length=20)

            class Meta:
                app_label = "dj_hyperview_database"

            def __str__(self) -> str:
                return self.title

        assert not any(signal.has_listeners(Unrelated) for signal in SIGNALS)
        assert Collector(using="default").can_fast_delete(Unrelated.objects.all())


def test_a_stale_registration_never_reaches_the_template_handlers(
    signal_model: type[Model],
) -> None:
    """Handlers ignore a sender that is not a template model.

    Django 5.2 keeps no reference to a sender, so the registration of a collected
    proxy can match a later, unrelated class that reuses its id. Connecting the
    handlers to such a class stands in for that registration.
    """
    with isolate_apps():

        class Unrelated(Model):
            title = CharField(max_length=20)

            class Meta:
                app_label = "dj_hyperview_database"

            def __str__(self) -> str:
                return self.title

        database_signals._connect_model(Unrelated)
        with connection.schema_editor() as editor:
            editor.create_model(Unrelated)
        try:
            with patch(
                "dj_hyperview.contrib.database.signals._schedule_invalidation"
            ) as schedule:
                row = Unrelated.objects.create(title="screen.xml")
                row.delete()
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Unrelated)

    schedule.assert_not_called()


def test_connecting_a_model_again_never_doubles_its_invalidations(
    proxy_model: type[Model],
) -> None:
    """Repeated ready() calls reach the same registrations as the first."""
    database_signals._connect_model(proxy_model)
    _connect_signal_handlers()

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        proxy_model.objects.create(name="screen.xml", content="<view />").delete()

    assert schedule.call_args_list == [call("screen.xml", using="default")] * 2


def test_scoped_proxy_rename_invalidates_the_stored_name(
    signal_model: type[Model], scoped_proxy_model: type[Model]
) -> None:
    """The stored name comes from the base manager, which never filters rows."""
    stored = signal_model.objects.create(name="draft/home.xml", content="<view />")
    assert not scoped_proxy_model._default_manager.filter(pk=stored.pk).exists()
    scoped = scoped_proxy_model._base_manager.get(pk=stored.pk)

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        scoped.name = "live/home.xml"
        scoped.save()

    schedule.assert_called_once_with("draft/home.xml", "live/home.xml", using="default")


def test_scoped_proxy_delete_invalidates_the_stored_name(
    signal_model: type[Model], scoped_proxy_model: type[Model]
) -> None:
    stored = signal_model.objects.create(name="draft/home.xml", content="<view />")
    scoped = scoped_proxy_model._base_manager.get(pk=stored.pk)
    scoped.name = "live/unsaved.xml"

    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        scoped.delete()

    schedule.assert_called_once_with("draft/home.xml", using="default")


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


def test_model_save_avoids_redundant_nested_savepoint(
    signal_model: type[Model],
) -> None:
    """The model keeps its transaction boundary without nested savepoint SQL."""
    template = signal_model.objects.create(name="screen.xml", content="<view />")

    with transaction.atomic(using="default"):
        with CaptureQueriesContext(connection) as queries:
            template.content = "<updated />"
            template.save(update_fields={"content"})

    sql = [query["sql"].lstrip().upper() for query in queries]
    assert not any(statement.startswith("SAVEPOINT") for statement in sql)
    assert not any(statement.startswith("RELEASE SAVEPOINT") for statement in sql)


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


PROXY_APP_MODELS = """\
from dj_hyperview.contrib.database.models import HyperviewTemplate


class Draft(HyperviewTemplate):
    class Meta:
        proxy = True


class Nested(Draft):
    class Meta:
        proxy = True
"""
PROXY_PROJECT_SETTINGS = """\
from tests.settings_database_admin import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "proxyapp"]  # noqa: F405
"""
PROXY_PROJECT_PROBE = """\
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import django

django.setup()

from django.apps import apps
from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models.deletion import Collector
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from proxyapp.models import Draft, Nested

from dj_hyperview.contrib.database.models import HyperviewTemplate

signals = (pre_save, post_save, pre_delete, post_delete)
# A second ready() call must reach the registrations of the first.
apps.get_app_config("dj_hyperview_database").ready()
with connection.schema_editor() as editor:
    editor.create_model(HyperviewTemplate)
scheduled = []
with patch(
    "dj_hyperview.contrib.database.signals._schedule_invalidation",
    lambda *names, using: scheduled.append(names),
):
    Nested.objects.create(name="screen.xml", content="<view />").delete()
print(
    json.dumps(
        {
            "session_listeners": any(s.has_listeners(Session) for s in signals),
            "session_fast_delete": Collector(using="default").can_fast_delete(
                Session.objects.all()
            ),
            "template_listeners": [
                all(s.has_listeners(model) for s in signals)
                for model in (HyperviewTemplate, Draft, Nested)
            ],
            "scheduled": scheduled,
        }
    )
)
"""


def test_ready_connects_template_models_and_their_proxies_only(tmp_path: Path) -> None:
    """A project keeps Django's bulk delete while its own proxies still invalidate."""
    app = tmp_path / "proxyapp"
    app.mkdir()
    (app / "__init__.py").touch()
    (app / "models.py").write_text(PROXY_APP_MODELS, encoding="utf-8")
    (tmp_path / "proxy_project.py").write_text(PROXY_PROJECT_SETTINGS, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(PROXY_PROJECT_PROBE, encoding="utf-8")

    result = run_isolated(
        "proxy_project", f"import runpy; runpy.run_path({str(probe)!r})"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "session_listeners": False,
        "session_fast_delete": True,
        "template_listeners": [True, True, True],
        "scheduled": [["screen.xml"], ["screen.xml"]],
    }


@pytest.mark.parametrize("fields", [["content"], ["name"], ["content", "name"], []])
def test_save_accepts_single_use_update_fields(
    signal_model: type[Model], fields: list[str]
) -> None:
    """Iterables retain every requested field and keep name identities aligned."""
    from dj_hyperview.contrib.database._identity import template_name_identity

    template = signal_model.objects.create(name="old.xml", content="<old />")
    template.name = "new.xml"
    template.content = "<new />"
    with patch(
        "dj_hyperview.contrib.database.signals._schedule_invalidation"
    ) as schedule:
        template.save(update_fields=(field for field in fields))
    template.refresh_from_db()

    expected_name = "new.xml" if "name" in fields else "old.xml"
    assert template.name == expected_name
    assert template.name_identity == template_name_identity(expected_name)
    assert template.content == ("<new />" if "content" in fields else "<old />")
    if "name" in fields:
        schedule.assert_called_once_with("old.xml", "new.xml", using="default")
    elif fields:
        schedule.assert_called_once_with("old.xml", using="default")
    else:
        schedule.assert_not_called()


@pytest.mark.django_db(transaction=True, databases=ALIASES)
@pytest.mark.parametrize("rollback", [False, True])
def test_nested_cross_database_delete_with_matching_primary_key(
    dual_signal_model: type[Model], rollback: bool
) -> None:
    """Batch suppression applies only to rows in the same database alias."""
    default = dual_signal_model.objects.using("default").create(
        name="default.xml", content="<default />"
    )
    replica = dual_signal_model.objects.using("replica").create(
        pk=default.pk, name="replica.xml", content="<replica />"
    )
    replica_pk = replica.pk

    def cleanup_replica(sender: type[Model], using: str, **kwargs: Any) -> None:
        if using == "default":
            replica.delete(using="replica")

    pre_delete.connect(cleanup_replica, sender=dual_signal_model, weak=False)
    try:
        with patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates"
        ) as invalidate:
            with transaction.atomic(using="default"):
                with transaction.atomic(using="replica"):
                    dual_signal_model.objects.using("default").filter(
                        pk=default.pk
                    ).delete()
                    invalidate.assert_not_called()
                    transaction.set_rollback(rollback, using="replica")
                if rollback:
                    invalidate.assert_not_called()
                else:
                    invalidate.assert_called_once_with("replica.xml")
                transaction.set_rollback(rollback, using="default")
            expected = [] if rollback else [("replica.xml",), ("default.xml",)]
            assert [call.args for call in invalidate.call_args_list] == expected
    finally:
        pre_delete.disconnect(cleanup_replica, sender=dual_signal_model)

    default_rows = dual_signal_model.objects.using("default").filter(pk=default.pk)
    replica_rows = dual_signal_model.objects.using("replica").filter(pk=replica_pk)
    assert default_rows.exists() is rollback
    assert replica_rows.exists() is rollback
