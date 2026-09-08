"""Multi-database and clean-process acceptance for the synthetic consumer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.package_guard import validate_project

from tests.consumer_project.process import PROJECT_ROOT, run_consumer


def _run_multidb(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Run a multi-database probe with isolated SQLite files."""
    return run_consumer(
        "tests.consumer_project.settings_multidb",
        source,
        database=tmp_path / "default.sqlite3",
    )


def test_selected_aliases_isolate_sources_services_admin_and_transactions(
    tmp_path: Path,
) -> None:
    """Reads, writes, cache entries, and commits remain alias-correct."""
    result = _run_multidb(
        tmp_path,
        r"""
import json
from unittest.mock import patch
import django

django.setup()

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, transaction
from django.test import Client, override_settings
from django.urls import reverse
from dj_hyperview import invalidate_templates
from dj_hyperview.contrib.database.services import publish_template
from tests.consumer_project import routing

for alias in ("default", "replica"):
    call_command("migrate", database=alias, verbosity=0)
model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
for alias, content in (("default", "default"), ("replica", "replica")):
    model.objects.using(alias).create(
        name="screen.xml", content=(
            f"<view xmlns='https://hyperview.org/hyperview'>"
            f"<text>{content}</text>"
            f"</view>"
        )
    )
    model.objects.using(alias).create(
        name="atomic.xml", content=(
            f"<view xmlns='https://hyperview.org/hyperview'>"
            f"<text>{content}-old</text>"
            f"</view>"
        )
    )
client = Client()

def source_config(alias):
    return {
        "SOURCES": [{
            "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
            "OPTIONS": {"using": alias},
        }],
        "CACHE": {
            "ALIAS": "default", "NAMESPACE": "consumer-multidb",
            "TTL": 300, "NEGATIVE_TTL": 30,
        },
    }

def document(name, alias):
    with override_settings(HYPERVIEW=source_config(alias)):
        return client.get("/documents/source/", {"template": name})

default_first = document("screen.xml", "default")
replica_first = document("screen.xml", "replica")
model.objects.using("default").filter(name="screen.xml").update(
    content=(
        "<view xmlns='https://hyperview.org/hyperview'>"
        "<text>default-new</text>"
        "</view>"
    ), revision=2
)
default_cached = document("screen.xml", "default")
replica_cached = document("screen.xml", "replica")
invalidate_templates("screen.xml")
default_fresh = document("screen.xml", "default")

routing.WRITE_ALIAS = "replica"
routing.READ_ALIAS = "replica"
router_read = client.get("/documents/source/", {"template": "screen.xml"})
events = []
with patch(
    "dj_hyperview.contrib.database._invalidation.invalidate_templates",
    side_effect=lambda *names: events.append(list(names)),
):
    with transaction.atomic(using="default"):
        with transaction.atomic(using="replica"):
            routed = publish_template("routed.xml", (
                "<view xmlns='https://hyperview.org/hyperview'>"
                "<text>service</text>"
                "</view>"
            ))
            before_replica_commit = list(events)
        after_replica_commit = list(events)
        default_still_open = connections["default"].in_atomic_block

account = get_user_model().objects.db_manager("default").create_superuser(
    username="administrator", password="secret"
)
client.force_login(account)
template = model.objects.using("replica").get(name="routed.xml")
change = reverse(
    "admin:dj_hyperview_database_hyperviewtemplate_change", args=[template.pk]
)
admin_response = client.post(change, {
    "name": "routed.xml", "content": (
        "<view xmlns='https://hyperview.org/hyperview'>"
        "<text>admin</text>"
        "</view>"
    ), "active": "on",
    "expected_revision": "1", "_save": "Save",
})

try:
    with transaction.atomic(using="default"):
        publish_template(
            "atomic.xml", (
                "<view xmlns='https://hyperview.org/hyperview'>"
                "<text>default-rollback</text>"
                "</view>"
            ),
            expected_revision=1, using="default",
        )
        with transaction.atomic(using="replica"):
            publish_template(
                "atomic.xml", (
                    "<view xmlns='https://hyperview.org/hyperview'>"
                    "<text>replica-commit</text>"
                    "</view>"
                ),
                expected_revision=1, using="replica",
            )
        raise RuntimeError("rollback")
except RuntimeError:
    pass

print(json.dumps({
    "cache": [
        default_first.content.decode(), replica_first.content.decode(),
        default_cached.content.decode(), replica_cached.content.decode(),
        default_fresh.content.decode(),
    ],
    "callback": [before_replica_commit, after_replica_commit, default_still_open],
    "service": [routed.name, routed.revision, routed.created],
    "router_read": router_read.content.decode(),
    "admin": [
        admin_response.status_code,
        model.objects.using("replica").get(name="routed.xml").content,
        model.objects.using("default").filter(name="routed.xml").exists(),
    ],
    "transactions": [
        model.objects.using("default").get(name="atomic.xml").content,
        model.objects.using("replica").get(name="atomic.xml").content,
    ],
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cache": [
            "<view xmlns='https://hyperview.org/hyperview'><text>default</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>replica</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>default-new</text>"
            "</view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>replica</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>default-new</text>"
            "</view>",
        ],
        "callback": [[], [["routed.xml"]], True],
        "service": ["routed.xml", 1, True],
        "router_read": (
            "<view xmlns='https://hyperview.org/hyperview'><text>replica</text></view>"
        ),
        "admin": [
            302,
            "<view xmlns='https://hyperview.org/hyperview'><text>admin</text></view>",
            False,
        ],
        "transactions": [
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>default-old</text>"
            "</view>",
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>replica-commit</text>"
            "</view>",
        ],
    }


def test_alias_and_backend_failures_are_closed_and_redacted(tmp_path: Path) -> None:
    """Unavailable aliases and databases expose only stable public errors."""
    result = _run_multidb(
        tmp_path,
        r"""
import json
import django

django.setup()

from dj_hyperview.contrib.database.sources import DatabaseSource
from dj_hyperview.exceptions import SourceUnavailable

outcomes = []
aliases = (("private-alias", "alias unavailable"), ("broken", "query failed"))
for alias, reason in aliases:
    try:
        DatabaseSource(using=alias).resolve("private/screen.xml")
    except SourceUnavailable as error:
        outcomes.append([
            str(error), error.__cause__ is None, error.__context__ is None,
            "private" not in str(error) and "sqlite" not in str(error),
            reason == error.reason,
        ])
print(json.dumps(outcomes))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        [
            "Template source unavailable: database (alias unavailable)",
            True,
            True,
            True,
            True,
        ],
        [
            "Template source unavailable: database (query failed)",
            True,
            True,
            True,
            True,
        ],
    ]


def test_multidatabase_consumer_starts_without_external_imports_or_io(
    tmp_path: Path,
) -> None:
    """A clean process loads public URLs while hostile integrations stay blocked."""
    result = _run_multidb(
        tmp_path,
        r"""
import importlib.abc
import json
import socket
import sys

class ForbiddenImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = {"django_hv", "apps", "backend", "config", "redis"}
        if fullname.split(".")[0] in forbidden:
            raise AssertionError("external import attempted")
        return None

def forbidden_io(*args, **kwargs):
    raise AssertionError("startup I/O attempted")

sys.meta_path.insert(0, ForbiddenImport())
from django.core.cache import CacheHandler
from django.db.backends.base.base import BaseDatabaseWrapper
CacheHandler.__getitem__ = forbidden_io
BaseDatabaseWrapper.cursor = forbidden_io
socket.create_connection = forbidden_io

import django
django.setup()
from django.urls import get_resolver
from dj_hyperview import TemplateResolver
from dj_hyperview.contrib.database.services import publish_template

print(json.dumps({
    "patterns": len(get_resolver().url_patterns),
    "public": [TemplateResolver.__name__, publish_template.__name__],
    "forbidden": any(
        name.split(".")[0] in {"django_hv", "apps", "backend", "config", "redis"}
        for name in sys.modules
    ),
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "patterns": 2,
        "public": ["TemplateResolver", "publish_template"],
        "forbidden": False,
    }
    assert validate_project(PROJECT_ROOT) == []
    assert not list((PROJECT_ROOT / "src" / "dj_hyperview").rglob("*.xml"))
    assert not list((PROJECT_ROOT / "src" / "dj_hyperview").rglob("*.hxml"))
