"""Consumer acceptance for transactional database administration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.consumer_project.process import run_consumer


def _run_admin_consumer(
    tmp_path: Path, source: str
) -> subprocess.CompletedProcess[str]:
    """Run one isolated admin/cache scenario against temporary SQLite."""
    return run_consumer(
        "tests.consumer_project.settings_admin_postcommit",
        source,
        database=tmp_path / "consumer.sqlite3",
    )


def test_publication_invalidation_waits_for_commit_and_skips_rollback(
    tmp_path: Path,
) -> None:
    """Cached HTTP content changes only after its database transaction commits."""
    result = _run_admin_consumer(
        tmp_path,
        r"""
import json
import django

django.setup()

from django.core.management import call_command
from django.db import transaction
from django.test import Client
from dj_hyperview.contrib.database.services import publish_template

call_command("migrate", verbosity=0)
client = Client()
url = "/documents/source/?template=screen.xml"
publish_template("screen.xml", (
    "<view xmlns='https://hyperview.org/hyperview'>"
    "<text>old</text>"
    "</view>"
), using="default")
initial = client.get(url).content.decode()

try:
    with transaction.atomic(using="default"):
        publish_template(
            "screen.xml", (
                "<view xmlns='https://hyperview.org/hyperview'>"
                "<text>rolled back</text>"
                "</view>"
            ),
            expected_revision=1, using="default",
        )
        during_rollback = client.get(url).content.decode()
        raise RuntimeError("rollback")
except RuntimeError:
    pass
after_rollback = client.get(url).content.decode()

with transaction.atomic(using="default"):
    publication = publish_template(
        "screen.xml", (
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>committed</text>"
            "</view>"
        ),
        expected_revision=1, using="default",
    )
    during_commit = client.get(url).content.decode()
after_commit = client.get(url)
print(json.dumps({
    "content": [
        initial, during_rollback, after_rollback,
        during_commit, after_commit.content.decode(),
    ],
    "database": publication.name,
    "revision": publication.revision,
    "source": after_commit.headers["X-Hyperview-Source"],
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "content": [
            "<view xmlns='https://hyperview.org/hyperview'><text>old</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>old</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>old</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>old</text></view>",
            (
                "<view xmlns='https://hyperview.org/hyperview'>"
                "<text>committed</text>"
                "</view>"
            ),
        ],
        "database": "screen.xml",
        "revision": 2,
        "source": "database",
    }


def test_admin_edit_rename_and_delete_refresh_cached_requests(tmp_path: Path) -> None:
    """Committed admin mutations rotate cached old and new template names."""
    result = _run_admin_consumer(
        tmp_path,
        r"""
import json
import django

django.setup()

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from dj_hyperview.contrib.database.services import publish_template

call_command("migrate", verbosity=0)
account = get_user_model().objects.create_superuser(
    username="administrator", password="secret"
)
client = Client()
client.force_login(account)
model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
publish_template("admin-old.xml", (
    "<view xmlns='https://hyperview.org/hyperview'>"
    "<text>old</text>"
    "</view>"
), using="default")
template = model.objects.get(name="admin-old.xml")
prefix = "admin:dj_hyperview_database_hyperviewtemplate"
change = reverse(f"{prefix}_change", args=[template.pk])
old_url = "/documents/source/?template=admin-old.xml"
new_url = "/documents/source/?template=admin-new.xml"
before = client.get(old_url).content.decode()

edited = client.post(change, {
    "name": "admin-old.xml", "content": (
        "<view xmlns='https://hyperview.org/hyperview'>"
        "<text>edited</text>"
        "</view>"
    ),
    "active": "on", "expected_revision": "1", "_save": "Save",
})
after_edit = client.get(old_url).content.decode()
renamed = client.post(change, {
    "name": "admin-new.xml", "content": (
        "<view xmlns='https://hyperview.org/hyperview'>"
        "<text>renamed</text>"
        "</view>"
    ),
    "active": "on", "expected_revision": "2", "_save": "Save",
})
old_after_rename = client.get(old_url)
new_after_rename = client.get(new_url)
template.refresh_from_db()
delete = reverse(f"{prefix}_delete", args=[template.pk])
deleted = client.post(delete, {"post": "yes", "expected_revision": "3"})
after_delete = client.get(new_url)
print(json.dumps({
    "statuses": [
        edited.status_code, renamed.status_code, old_after_rename.status_code,
        new_after_rename.status_code, deleted.status_code, after_delete.status_code,
    ],
    "content": [before, after_edit, new_after_rename.content.decode()],
    "database": template._state.db,
    "remaining": model.objects.count(),
    "revision": template.revision,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "statuses": [302, 302, 404, 200, 302, 404],
        "content": [
            "<view xmlns='https://hyperview.org/hyperview'><text>old</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>edited</text></view>",
            "<view xmlns='https://hyperview.org/hyperview'><text>renamed</text></view>",
        ],
        "database": "default",
        "remaining": 0,
        "revision": 3,
    }


def test_admin_postcommit_cache_failure_preserves_database_commit(
    tmp_path: Path,
) -> None:
    """A failing cache callback stays visible after the database mutation commits."""
    result = _run_admin_consumer(
        tmp_path,
        r"""
import json
from unittest.mock import patch
import django

django.setup()

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from dj_hyperview.contrib.database.services import publish_template
from dj_hyperview.exceptions import SourceUnavailable

call_command("migrate", verbosity=0)
account = get_user_model().objects.create_superuser(
    username="administrator", password="secret"
)
client = Client()
client.force_login(account)
model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
publish_template("failure.xml", (
    "<view xmlns='https://hyperview.org/hyperview'>"
    "<text>old</text>"
    "</view>"
), using="default")
template = model.objects.get(name="failure.xml")
change = reverse(
    "admin:dj_hyperview_database_hyperviewtemplate_change", args=[template.pk]
)
observed = False
try:
    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates",
        side_effect=SourceUnavailable("cache:test", "failure"),
    ):
        client.post(change, {
            "name": "failure.xml", "content": (
                "<view xmlns='https://hyperview.org/hyperview'>"
                "<text>committed</text>"
                "</view>"
            ),
            "active": "on", "expected_revision": "1", "_save": "Save",
        })
except SourceUnavailable:
    observed = True
template.refresh_from_db()
print(json.dumps({
    "observed": observed,
    "content": template.content,
    "database": template._state.db,
    "revision": template.revision,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "observed": True,
        "content": (
            "<view xmlns='https://hyperview.org/hyperview'>"
            "<text>committed</text>"
            "</view>"
        ),
        "database": "default",
        "revision": 2,
    }
