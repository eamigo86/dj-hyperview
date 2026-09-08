"""Read-only integrity diagnostics for historical database template rows."""

from collections.abc import Iterator
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.models import Model, QuerySet
from django.test.utils import CaptureQueriesContext

from dj_hyperview.contrib.database._identity import template_name_identity
from tests.test_database_app import run_isolated

ALIASES = ("default", "replica")


def integrity_command() -> BaseCommand:
    from dj_hyperview.contrib.database.management.commands import (
        check_hyperview_templates,
    )

    return check_hyperview_templates.Command()


@pytest.fixture
def integrity_model(django_db_blocker: Any) -> Iterator[type[Model]]:
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


@pytest.mark.django_db(transaction=True, databases=ALIASES)
@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize("count", [0, 2])
def test_integrity_command_accepts_healthy_rows_and_empty_database(
    integrity_model: type[Model], alias: str, count: int
) -> None:
    for index in range(count):
        integrity_model.objects.using(alias).create(
            name=f"screen-{index}.xml", content="<private>secret</private>"
        )
    output = StringIO()

    call_command(integrity_command(), database=alias, stdout=output)

    assert output.getvalue() == (
        f"Checked {count} templates on database '{alias}': no integrity issues.\n"
    )


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_integrity_command_reports_corrupt_duplicate_and_invalid_rows_without_writes(
    integrity_model: type[Model],
) -> None:
    rows = [
        integrity_model(
            pk=10,
            name="duplicate.xml",
            name_identity=template_name_identity("duplicate.xml"),
            content="<private>secret-one</private>",
        ),
        integrity_model(
            pk=20,
            name="duplicate.xml",
            name_identity="0" * 64,
            content="<private>secret-two</private>",
        ),
        integrity_model(
            pk=30,
            name="../private.xml",
            name_identity="1" * 64,
            content="<private>secret-three</private>",
        ),
    ]
    integrity_model._base_manager.using("replica").bulk_create(rows)
    before = list(integrity_model.objects.using("replica").order_by("pk").values())
    output = StringIO()
    calls: list[tuple[tuple[str, ...], int]] = []
    original_iterator = QuerySet.iterator

    def tracked_iterator(queryset: QuerySet, chunk_size: int) -> Iterator[Any]:
        calls.append((queryset.query.values_select, chunk_size))
        return original_iterator(queryset, chunk_size=chunk_size)

    with (
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections["replica"]) as replica_queries,
        patch.object(QuerySet, "iterator", tracked_iterator),
        patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates"
        ) as invalidate,
        pytest.raises(CommandError, match="4 integrity issues"),
    ):
        call_command(integrity_command(), database="replica", stdout=output)

    assert output.getvalue() == (
        "pk=20: identity_mismatch, duplicate_identity (first_pk=10)\n"
        "pk=30: invalid_name, identity_mismatch\n"
    )
    assert calls == [(("pk", "name", "name_identity"), 1000)]
    assert len(default_queries) == 0
    assert len(replica_queries) == 1
    sql = replica_queries[0]["sql"].upper()
    assert sql.startswith("SELECT")
    assert '"CONTENT"' not in sql
    assert '"REVISION"' not in sql
    invalidate.assert_not_called()
    after = list(integrity_model.objects.using("replica").order_by("pk").values())
    assert after == before


@pytest.mark.django_db(transaction=True, databases=ALIASES)
@pytest.mark.parametrize("name", ["../invalid.xml", "x" * 256])
def test_integrity_command_checks_name_even_when_identity_matches(
    integrity_model: type[Model], name: str
) -> None:
    integrity_model._base_manager.bulk_create(
        [
            integrity_model(
                pk=1,
                name=name,
                name_identity=template_name_identity(name),
                content="private",
            )
        ]
    )
    output = StringIO()
    with pytest.raises(CommandError, match="1 integrity issue"):
        call_command(integrity_command(), database="default", stdout=output)
    assert output.getvalue() == "pk=1: invalid_name\n"


def test_integrity_command_requires_explicit_valid_database_alias() -> None:
    with pytest.raises(CommandError, match="--database"):
        call_command(integrity_command())
    with pytest.raises(CommandError, match="database"):
        call_command(integrity_command(), database="unknown")


def test_integrity_command_registration_and_import_do_not_query_database() -> None:
    result = run_isolated(
        "tests.settings_database",
        "import django; from unittest.mock import patch; "
        "from django.db.backends.utils import CursorWrapper; "
        "p=patch.object(CursorWrapper, 'execute', side_effect=AssertionError('SQL')); "
        "p.start(); django.setup(); "
        "from django.core.management import get_commands, load_command_class; "
        "app=get_commands()['check_hyperview_templates']; "
        "load_command_class(app, 'check_hyperview_templates'); "
        "print(app); p.stop()",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "dj_hyperview.contrib.database"
