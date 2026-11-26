"""Database template source integration tests."""

import importlib
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import InterfaceError, connection
from django.db.models import Model
from django.test import override_settings
from pytest_django.fixtures import DjangoAssertNumQueries

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
    """Database router that records every read it routes."""

    def __init__(self) -> None:
        """Start with no recorded reads."""
        self.reads = []

    def db_for_read(self, model: type[Model], **hints: object) -> str:
        """Record the read and route it to the default alias.

        Args:
            model: Model class that the router is asked about.
            **hints: Routing hints that Django passes.

        Returns:
            The default database alias.
        """
        self.reads.append((model, hints))
        return "default"


@pytest.fixture
def database_model(transactional_db: None) -> Iterator[type[Model]]:
    """Create the template table for one test.

    Args:
        transactional_db: Enables transactional database access for the fixture.

    Yields:
        The template model class, with its table present.
    """
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
        with connection.schema_editor() as editor:
            editor.create_model(model)
        yield model
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def database_source_class() -> Callable[..., TemplateSource]:
    """Import the database source class lazily.

    Returns:
        The database source class, which builds a source when called.
    """
    return importlib.import_module(
        "dj_hyperview.contrib.database.sources"
    ).DatabaseSource


def test_database_source_module_import_is_lazy() -> None:
    """Importing the database source module does not import the models."""
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


def test_database_source_requires_installed_contrib_without_leaking_context() -> None:
    """Without the installed database app, the source fails with a stable error."""
    source = database_source_class()()

    with pytest.raises(SourceUnavailable) as captured:
        source.resolve("screen.xml")

    error = captured.value
    assert str(error) == "Template source unavailable: database (app unavailable)"
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    "failure", [AppRegistryNotReady("private registry"), LookupError("private model")]
)
def test_registry_failures_are_stable_and_redacted(failure: Exception) -> None:
    """Registry lookup failures become stable, redacted source errors.

    Args:
        failure: Registry error that the model lookup raises.
    """
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
    database_model: type[Model],
    django_assert_num_queries: DjangoAssertNumQueries,
    content: str,
) -> None:
    """An active template resolves with a single query.

    Args:
        database_model: Template model class with its table created for this test.
        django_assert_num_queries: Asserts how many queries run inside its block.
        content: Template content stored in the row, empty content included.
    """
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
    database_model: type[Model], django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """Inactive and absent templates are source misses.

    Args:
        database_model: Template model class with its table created for this test.
        django_assert_num_queries: Asserts how many queries run inside its block.
    """
    database_model.objects.create(name="inactive.xml", content="<view />", active=False)
    source = database_source_class()()

    with django_assert_num_queries(2):
        inactive = source.resolve("inactive.xml")
        absent = source.resolve("absent.xml")

    assert inactive is None
    assert absent is None


def test_name_is_canonicalized_before_registry_or_database_access() -> None:
    """Names are canonicalized before any registry or database access."""
    module = importlib.import_module("dj_hyperview.contrib.database.sources")

    with patch.object(
        module, "_template_model", side_effect=AssertionError("must not access apps")
    ):
        with pytest.raises(InvalidTemplateName):
            module.DatabaseSource().resolve("../private.xml")


def test_default_manager_honors_database_router(
    database_model: type[Model], django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """The default manager honors the database router.

    Args:
        database_model: Template model class with its table created for this test.
        django_assert_num_queries: Asserts how many queries run inside its block.
    """
    database_model.objects.create(name="screen.xml", content="<view />")
    router = RecordingRouter()

    with override_settings(DATABASE_ROUTERS=[router]), django_assert_num_queries(1):
        resolved = database_source_class()().resolve("screen.xml")

    assert resolved.content == "<view />"
    assert router.reads == [(database_model, {})]


def test_uncached_reads_observe_database_updates(database_model: type[Model]) -> None:
    """Uncached reads observe database updates.

    Args:
        database_model: Template model class with its table created for this test.
    """
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
    database_model: type[Model], tmp_path: Path
) -> None:
    """The resolver keeps the order of the database and filesystem sources.

    Args:
        database_model: Template model class with its table created for this test.
        tmp_path: Temporary directory that holds the filesystem template.
    """
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
    database_model: type[Model], tmp_path: Path
) -> None:
    """A damaged database identity behaves as a miss, not an outage.

    Args:
        database_model: Template model class with its table created for this test.
        tmp_path: Temporary directory that holds the filesystem template.
    """
    template = database_model.objects.create(name="screen.xml", content="database")
    database_model._base_manager.filter(pk=template.pk).update(name="renamed.xml")
    (tmp_path / "screen.xml").write_text("filesystem", encoding="utf-8")

    resolved = TemplateResolver(
        [database_source_class()(), FileSystemSource([tmp_path])]
    ).resolve("screen.xml")

    assert resolved.content == "filesystem"
    assert resolved.source == "filesystem"


def test_configured_source_passes_checks_and_resolver_integration(
    database_model: type[Model],
) -> None:
    """A configured database source passes the checks and resolves templates.

    Args:
        database_model: Template model class with its table created for this test.
    """
    database_model.objects.create(name="screen.xml", content="configured", revision=3)
    configured = {
        "SOURCES": [{"BACKEND": DATABASE_BACKEND, "OPTIONS": {"using": "default"}}]
    }

    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert (resolved.content, resolved.revision) == ("configured", "3")


def test_configured_source_without_contrib_fails_before_resolver_construction() -> None:
    """A configured source without the database app fails before the resolver exists."""
    configured = {"SOURCES": [{"BACKEND": DATABASE_BACKEND}]}

    with override_settings(HYPERVIEW=configured):
        with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E010"):
            TemplateResolver.from_settings()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("using", "reason"), [(None, "query failed"), ("missing", "alias unavailable")]
)
def test_database_infrastructure_failures_are_stable_and_redacted(
    using: str | None, reason: str
) -> None:
    """Database infrastructure failures become stable, redacted source errors.

    Args:
        using: Database alias the source reads from, or None for the default.
        reason: Public failure reason the error is expected to carry.
    """
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


def test_programming_errors_are_not_converted_to_source_failures(
    database_model: type[Model],
) -> None:
    """Programming errors are not converted into source failures.

    Args:
        database_model: Template model class with its table created for this test.
    """
    failure = RuntimeError("programming failure")
    manager = database_model._default_manager

    with patch.object(manager, "get", side_effect=failure):
        with pytest.raises(RuntimeError) as captured:
            database_source_class()().resolve("screen.xml")

    assert captured.value is failure


def test_interface_errors_become_redacted_source_failures(
    database_model: type[Model],
) -> None:
    """Closed pooled connections preserve the public source error contract.

    Args:
        database_model: Template model class with its table created for this test.
    """
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
