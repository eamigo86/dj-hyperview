import importlib
import traceback
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import InterfaceError, connection
from django.test import override_settings

from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.exceptions import (
    HyperviewConfigurationError,
    InvalidTemplateName,
    SourceUnavailable,
)
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import FileSystemSource, ResolvedTemplate, TemplateSource
from tests.test_database_app import run_isolated

DATABASE_APPS = ["dj_hyperview", "dj_hyperview.contrib.database"]
DATABASE_BACKEND = "dj_hyperview.contrib.database.sources.DatabaseSource"


def _assert_redacted_source_error(
    error: SourceUnavailable,
    *,
    source: str,
    reason: str,
    secrets: tuple[str, ...] = (),
) -> None:
    assert error.source == source
    assert error.reason == reason
    assert str(error) == f"Template source unavailable: {source} ({reason})"
    assert error.__cause__ is None
    assert error.__context__ is None
    for secret in secrets:
        assert secret not in str(error)
        assert secret not in repr(error)


class RecordingRouter:
    def __init__(self):
        self.reads = []

    def db_for_read(self, model, **hints):
        self.reads.append((model, hints))
        return "default"


@pytest.fixture
def database_model(transactional_db):
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
        with connection.schema_editor() as editor:
            editor.create_model(model)
        yield model
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def database_source_class():
    return importlib.import_module(
        "dj_hyperview.contrib.database.sources"
    ).DatabaseSource


def test_database_source_module_import_is_lazy():
    result = run_isolated(
        "tests.settings",
        "import sys, dj_hyperview.contrib.database; "
        "print('dj_hyperview.contrib.database.sources' in sys.modules, "
        "'dj_hyperview.contrib.database.models' in sys.modules); "
        "import dj_hyperview.contrib.database.sources; "
        "print('dj_hyperview.contrib.database.sources' in sys.modules, "
        "'dj_hyperview.contrib.database.models' in sys.modules)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["False False", "True False"]
    assert isinstance(database_source_class()(), TemplateSource)


def test_database_source_requires_installed_contrib_without_leaking_context():
    source = database_source_class()()

    with pytest.raises(SourceUnavailable) as captured:
        source.resolve("screen.xml")

    error = captured.value
    assert str(error) == "Template source unavailable: database (app unavailable)"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_redaction_contract_ignores_external_traceback_filenames() -> None:
    """Redaction checks inspect exception data rather than checkout paths."""
    scope: dict[str, object] = {
        "error": SourceUnavailable("database", "app unavailable")
    }
    try:
        exec(
            compile("raise error", "/private/tmp/consumer_probe.py", "exec"),
            scope,
        )
    except SourceUnavailable as error:
        assert "/private/tmp" in "".join(traceback.format_exception(error))
        _assert_redacted_source_error(
            error,
            source="database",
            reason="app unavailable",
            secrets=("private registry",),
        )


@pytest.mark.parametrize(
    "failure", [AppRegistryNotReady("private registry"), LookupError("private model")]
)
def test_registry_failures_are_stable_and_redacted(failure):
    module = importlib.import_module("dj_hyperview.contrib.database.sources")

    with patch.object(module.apps, "get_model", side_effect=failure):
        with pytest.raises(SourceUnavailable) as captured:
            module.DatabaseSource().resolve("screen.xml")

    _assert_redacted_source_error(
        captured.value,
        source="database",
        reason="app unavailable",
        secrets=("private registry", "private model"),
    )


@pytest.mark.parametrize("content", ["<view>stored</view>", ""])
def test_active_template_resolves_in_one_query(
    database_model, django_assert_num_queries, content
):
    database_model.objects.create(name="screens/home.xml", content=content, revision=7)

    with django_assert_num_queries(1):
        resolved = database_source_class()().resolve("screens/home.xml")

    assert resolved == ResolvedTemplate(
        name="screens/home.xml",
        content=content,
        origin="database:screens/home.xml",
        source="database",
        revision="7",
    )


def test_inactive_and_absent_templates_are_source_misses(
    database_model, django_assert_num_queries
):
    database_model.objects.create(name="inactive.xml", content="<view />", active=False)
    source = database_source_class()()

    with django_assert_num_queries(2):
        inactive = source.resolve("inactive.xml")
        absent = source.resolve("absent.xml")

    assert inactive is None
    assert absent is None


def test_name_is_canonicalized_before_registry_or_database_access():
    module = importlib.import_module("dj_hyperview.contrib.database.sources")

    with patch.object(
        module, "_template_model", side_effect=AssertionError("must not access apps")
    ):
        with pytest.raises(InvalidTemplateName):
            module.DatabaseSource().resolve("../private.xml")


def test_default_manager_honors_database_router(
    database_model, django_assert_num_queries
):
    database_model.objects.create(name="screen.xml", content="<view />")
    router = RecordingRouter()

    with override_settings(DATABASE_ROUTERS=[router]), django_assert_num_queries(1):
        resolved = database_source_class()().resolve("screen.xml")

    assert resolved.content == "<view />"
    assert router.reads == [(database_model, {})]


def test_uncached_reads_observe_database_updates(database_model):
    database_model.objects.create(
        name="screen.xml", content="<view>old</view>", revision=1
    )
    source = database_source_class()()

    before = source.resolve("screen.xml")
    database_model.objects.filter(name="screen.xml").update(
        content="<view>new</view>", revision=2
    )
    after = source.resolve("screen.xml")

    assert (before.content, before.revision) == ("<view>old</view>", "1")
    assert (after.content, after.revision) == ("<view>new</view>", "2")


def test_resolver_preserves_database_and_filesystem_precedence(
    database_model, tmp_path
):
    database_model.objects.create(name="screen.xml", content="database")
    (tmp_path / "screen.xml").write_text("filesystem", encoding="utf-8")
    database = database_source_class()()
    filesystem = FileSystemSource([tmp_path])

    assert TemplateResolver([database, filesystem]).resolve("screen.xml").content == (
        "database"
    )
    assert TemplateResolver([filesystem, database]).resolve("screen.xml").content == (
        "filesystem"
    )
    database_model.objects.filter(name="screen.xml").update(active=False)
    assert TemplateResolver([database, filesystem]).resolve("screen.xml").content == (
        "filesystem"
    )


def test_database_identity_divergence_falls_through_to_next_source(
    database_model, tmp_path
) -> None:
    """A damaged database identity behaves as a miss, not an outage."""
    template = database_model.objects.create(name="screen.xml", content="database")
    database_model._base_manager.filter(pk=template.pk).update(name="renamed.xml")
    (tmp_path / "screen.xml").write_text("filesystem", encoding="utf-8")

    resolved = TemplateResolver(
        [database_source_class()(), FileSystemSource([tmp_path])]
    ).resolve("screen.xml")

    assert resolved.content == "filesystem"
    assert resolved.source == "filesystem"


def test_configured_source_passes_checks_and_resolver_integration(database_model):
    database_model.objects.create(name="screen.xml", content="configured", revision=3)
    configured = {
        "SOURCES": [{"BACKEND": DATABASE_BACKEND, "OPTIONS": {"using": "default"}}]
    }

    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert (resolved.content, resolved.revision) == ("configured", "3")


def test_configured_source_without_contrib_fails_before_resolver_construction():
    configured = {"SOURCES": [{"BACKEND": DATABASE_BACKEND}]}

    with override_settings(HYPERVIEW=configured):
        with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E010"):
            TemplateResolver.from_settings()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("using", "reason"), [(None, "query failed"), ("missing", "alias unavailable")]
)
def test_database_infrastructure_failures_are_stable_and_redacted(using, reason):
    sensitive_name = "private/screen.xml"
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        source = database_source_class()(using=using)
        with pytest.raises(SourceUnavailable) as captured:
            source.resolve(sensitive_name)

    _assert_redacted_source_error(
        captured.value,
        source="database",
        reason=reason,
        secrets=(sensitive_name,),
    )


def test_programming_errors_are_not_converted_to_source_failures(database_model):
    failure = RuntimeError("programming failure")
    manager = database_model._default_manager

    with patch.object(manager, "get", side_effect=failure):
        with pytest.raises(RuntimeError) as captured:
            database_source_class()().resolve("screen.xml")

    assert captured.value is failure


def test_interface_errors_become_redacted_source_failures(database_model) -> None:
    """Closed pooled connections preserve the public source error contract."""
    manager = database_model._default_manager

    with patch.object(
        manager, "get", side_effect=InterfaceError("private connection state")
    ):
        with pytest.raises(SourceUnavailable) as captured:
            database_source_class()().resolve("screen.xml")

    _assert_redacted_source_error(
        captured.value,
        source="database",
        reason="query failed",
        secrets=("private connection state",),
    )
