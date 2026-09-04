"""HTTP acceptance tests for consumer template sources and safe failures."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from django.test import Client, override_settings

from tests.consumer_project import settings_filesystem as filesystem
from tests.consumer_project.process import run_consumer

SOURCE_HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
}


def _filesystem_config(
    root: Path, validation: dict[str, object] | None = None
) -> dict[str, object]:
    """Return isolated filesystem settings for one HTTP validation case."""
    return {
        "TEMPLATE_DIRS": [root],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        "VALIDATION": validation or {},
    }


@override_settings(**SOURCE_HTTP_SETTINGS)
def test_filesystem_source_document_exposes_safe_resolution_metadata(
    client: Client,
) -> None:
    """A resolved filesystem document retains content and source metadata."""
    response = client.get("/documents/source/", {"template": "precedence.xml"})

    expected = b"<view>primary</view>"
    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["X-Hyperview-Template"] == "precedence.xml"
    assert response.headers["X-Hyperview-Source"] == "filesystem"
    assert response.headers["X-Hyperview-Revision"] == sha256(expected).hexdigest()


@pytest.mark.parametrize(
    "hostile",
    ["../private-template.xml", "/absolute.xml", "nested//screen.xml"],
)
@override_settings(**SOURCE_HTTP_SETTINGS)
def test_unsafe_template_name_returns_redacted_bad_request(
    client: Client, hostile: str
) -> None:
    """Unsafe names are rejected without returning attacker-controlled input."""
    response = client.get("/documents/source/", {"template": hostile})

    assert response.status_code == 400
    assert response.headers["X-Hyperview-Error"] == "invalid_template_name"
    assert hostile.encode() not in response.content


@pytest.mark.parametrize(
    ("content", "validation", "code"),
    [
        ("<view>private-payload", {}, "malformed_xml"),
        ("<view>private-payload</view>", {"SCHEMA": lambda document: False}, "schema"),
        ("<view>private-payload</view>", {"MAX_BYTES": 4}, "max_bytes"),
        ("<v><a><b>private-payload</b></a></v>", {"MAX_DEPTH": 2}, "max_depth"),
        ("<v><a /><b>private-payload</b></v>", {"MAX_NODES": 2}, "max_nodes"),
    ],
)
def test_validation_failures_return_stable_safe_status(
    client: Client,
    tmp_path: Path,
    content: str,
    validation: dict[str, object],
    code: str,
) -> None:
    """Malformed, schema-invalid, and excessive HXML return safe 422 errors."""
    (tmp_path / "screen.xml").write_text(content, encoding="utf-8")

    with override_settings(
        ROOT_URLCONF="tests.consumer_project.urls",
        HYPERVIEW=_filesystem_config(tmp_path, validation),
    ):
        response = client.get("/documents/source/", {"template": "screen.xml"})

    assert response.status_code == 422
    assert response.headers["X-Hyperview-Error"] == code
    assert b"private-payload" not in response.content


def test_empty_content_is_not_a_miss_and_final_miss_is_404(
    client: Client, tmp_path: Path
) -> None:
    """Empty raw content reaches validation while a real source miss returns 404."""
    (tmp_path / "empty.xml").write_text("", encoding="utf-8")

    with override_settings(
        ROOT_URLCONF="tests.consumer_project.urls",
        HYPERVIEW=_filesystem_config(tmp_path),
    ):
        empty = client.get("/documents/source/", {"template": "empty.xml"})
        missing = client.get("/documents/source/", {"template": "missing.xml"})

    assert (empty.status_code, empty.headers["X-Hyperview-Error"]) == (
        422,
        "malformed_xml",
    )
    assert (missing.status_code, missing.headers["X-Hyperview-Error"]) == (
        404,
        "template_not_found",
    )


def test_database_cache_precedence_and_failure_modes_over_http(
    tmp_path: Path,
) -> None:
    """Optional source stacks preserve metadata, cache states, and failure policy."""
    root = tmp_path / "templates"
    root.mkdir()
    (root / "screen.xml").write_text("<view>old</view>", encoding="utf-8")
    (root / "empty.xml").write_text("", encoding="utf-8")
    result = run_consumer(
        "tests.consumer_project.settings_database",
        r"""
import json
from pathlib import Path
import django

django.setup()

from django.apps import apps
from django.core.management import call_command
from django.test import Client, override_settings
from dj_hyperview import TemplateResolver, invalidate_templates

call_command("migrate", "dj_hyperview_database", verbosity=0)
model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
model.objects.create(
    name="precedence.xml", content="<view>database</view>", revision=7
)
model.objects.create(
    name="fragments/item.xml", content="<view>inactive</view>", active=False
)
client = Client()
database = client.get("/documents/source/", {"template": "precedence.xml"})
fallback = client.get(
    "/documents/source/",
    {"template": "fragments/item.xml", "label": "fallback"},
)
origin = TemplateResolver.from_settings().resolve("precedence.xml").origin

root = Path(__import__("os").environ["DJ_HYPERVIEW_CONSUMER_TEMPLATES"])
base = {
    "TEMPLATE_DIRS": [root],
    "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
}
caches = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "source-http",
    },
    "failure": {
        "BACKEND": "tests.test_cache_fail_closed.ExplodingCache",
        "OPTIONS": {"OPERATION": "get"},
    },
}
cache = {
    "ALIAS": "default",
    "NAMESPACE": "source-http",
    "TTL": 300,
    "NEGATIVE_TTL": 30,
}
with override_settings(CACHES=caches, HYPERVIEW={**base, "CACHE": cache}):
    first = client.get("/documents/source/", {"template": "screen.xml"})
    (root / "screen.xml").write_text("<view>new</view>", encoding="utf-8")
    hit = client.get("/documents/source/", {"template": "screen.xml"})
    first_miss = client.get("/documents/source/", {"template": "missing.xml"})
    (root / "missing.xml").write_text("<view>created</view>", encoding="utf-8")
    cached_miss = client.get("/documents/source/", {"template": "missing.xml"})
    empty = client.get("/documents/source/", {"template": "empty.xml"})
    (root / "empty.xml").write_text("<view>filled</view>", encoding="utf-8")
    empty_hit = client.get("/documents/source/", {"template": "empty.xml"})
    invalidate_templates("screen.xml", "missing.xml", "empty.xml")
    refreshed = [
        client.get("/documents/source/", {"template": name})
        for name in ("screen.xml", "missing.xml", "empty.xml")
    ]

failure_cache = {**cache, "ALIAS": "failure"}
with override_settings(
    CACHES=caches,
    HYPERVIEW={**base, "CACHE": {**failure_cache, "FAILURE_MODE": "bypass"}},
):
    bypass = client.get("/documents/source/", {"template": "screen.xml"})
with override_settings(
    CACHES=caches,
    HYPERVIEW={**base, "CACHE": {**failure_cache, "FAILURE_MODE": "raise"}},
):
    raised = client.get("/documents/source/", {"template": "screen.xml"})

call_command("migrate", "dj_hyperview_database", "zero", verbosity=0)
db_failure = client.get("/documents/source/", {"template": "database-only.xml"})
print(json.dumps({
    "database": [
        database.status_code,
        database.content.decode(),
        database.headers["X-Hyperview-Source"],
        database.headers["X-Hyperview-Revision"],
        origin,
    ],
    "fallback": [
        fallback.status_code,
        fallback.content.decode(),
        fallback.headers["X-Hyperview-Source"],
    ],
    "cache": [
        first.content.decode(),
        hit.content.decode(),
        first_miss.status_code,
        cached_miss.status_code,
        empty.status_code,
        empty_hit.status_code,
        [item.content.decode() for item in refreshed],
    ],
    "failures": [
        bypass.status_code,
        bypass.content.decode(),
        raised.status_code,
        raised.headers["X-Hyperview-Error"],
        db_failure.status_code,
        db_failure.headers["X-Hyperview-Error"],
        "consumer-secret" in raised.content.decode(),
    ],
}))
""",
        database=tmp_path / "consumer.sqlite3",
        template_dir=root,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["database"][:4] == [200, "<view>database</view>", "database", "7"]
    assert data["database"][4] == "database:precedence.xml"
    assert data["fallback"] == [200, "<view><text>fallback</text></view>", "filesystem"]
    assert data["cache"][:6] == ["<view>old</view>"] * 2 + [404, 404, 422, 422]
    assert data["cache"][6] == [
        "<view>new</view>",
        "<view>created</view>",
        "<view>filled</view>",
    ]
    assert data["failures"] == [
        200,
        "<view>new</view>",
        503,
        "source_unavailable",
        503,
        "source_unavailable",
        False,
    ]
