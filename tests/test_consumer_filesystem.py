"""Filesystem integration tests for the package-owned Django consumer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from importlib import import_module
from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview import (
    HyperviewEngine,
    InvalidTemplateName,
    TemplateNotFound,
    TemplateResolver,
    TemplateValidationError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM_SETTINGS = import_module("tests.consumer_project.settings_filesystem")


def _run_filesystem_probe() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    python_path = [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)]
    if inherited := env.get("PYTHONPATH"):
        python_path.append(inherited)
    env.update(
        DJANGO_SETTINGS_MODULE="tests.consumer_project.settings_filesystem",
        PYTHONPATH=os.pathsep.join(python_path),
    )
    script = r"""
import json
import socket


def forbidden_optional_access(*args, **kwargs):
    raise AssertionError("optional infrastructure access attempted")


from django.core.cache import CacheHandler
from django.db.backends.base.base import BaseDatabaseWrapper

CacheHandler.__getitem__ = forbidden_optional_access
BaseDatabaseWrapper.cursor = forbidden_optional_access
socket.create_connection = forbidden_optional_access

import django

django.setup()

from django.core.checks import run_checks
from dj_hyperview import HyperviewEngine, TemplateResolver

resolver = TemplateResolver.from_settings()
rendered = HyperviewEngine(resolver).render("screens/full.xml", {"title": "Clean"})
print(json.dumps({
    "checks": [message.id for message in run_checks(tags=["dj_hyperview"])],
    "rendered": rendered,
    "sources": len(resolver.sources),
}))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_filesystem_consumer_resolves_a_full_document() -> None:
    """The public resolver loads the consumer-owned full document fixture."""
    with override_settings(HYPERVIEW=FILESYSTEM_SETTINGS.HYPERVIEW):
        resolved = TemplateResolver.from_settings().resolve("screens/full.xml")

    assert resolved.name == "screens/full.xml"
    assert resolved.source == "filesystem"
    assert (
        resolved.origin
        == (
            FILESYSTEM_SETTINGS.SECONDARY_TEMPLATE_DIR / "screens" / "full.xml"
        ).as_uri()
    )


def test_full_render_uses_one_precedence_rule_for_extends_and_include() -> None:
    """Root, parent, and included templates share ordered directory lookup."""
    with override_settings(HYPERVIEW=FILESYSTEM_SETTINGS.HYPERVIEW):
        resolver = TemplateResolver.from_settings()
        rendered = HyperviewEngine(resolver).render(
            "screens/full.xml", {"title": "A & B"}
        )
        layout = resolver.resolve("layouts/base.xml")
        included = resolver.resolve("parts/title.xml")

    assert rendered == (
        "<view><header>primary-layout</header><text>primary: A &amp; B</text></view>"
    )
    assert (
        layout.origin
        == (FILESYSTEM_SETTINGS.PRIMARY_TEMPLATE_DIR / "layouts" / "base.xml").as_uri()
    )
    assert (
        included.origin
        == (FILESYSTEM_SETTINGS.PRIMARY_TEMPLATE_DIR / "parts" / "title.xml").as_uri()
    )


def test_fragment_render_escapes_context_and_first_directory_wins() -> None:
    """Fragment rendering escapes values and keeps deterministic precedence."""
    with override_settings(HYPERVIEW=FILESYSTEM_SETTINGS.HYPERVIEW):
        resolver = TemplateResolver.from_settings()
        engine = HyperviewEngine(resolver)
        fragment = engine.render("fragments/item.xml", {"label": "<safe>"})
        precedence = engine.render("precedence.xml")

    assert fragment == "<view><text>&lt;safe&gt;</text></view>"
    assert precedence == "<view>primary</view>"


def test_empty_content_is_a_hit_and_miss_remains_typed() -> None:
    """Empty source content stays distinct from an unresolved template."""
    with override_settings(HYPERVIEW=FILESYSTEM_SETTINGS.HYPERVIEW):
        resolver = TemplateResolver.from_settings()
        empty = resolver.resolve("empty.xml")
        with pytest.raises(TemplateNotFound) as missing:
            resolver.resolve("missing.xml")
        with pytest.raises(TemplateValidationError) as invalid:
            HyperviewEngine(resolver).render("empty.xml")

    assert empty.content == ""
    assert empty.revision == sha256(b"").hexdigest()
    assert missing.value.name == "missing.xml"
    assert str(missing.value) == "Template not found: missing.xml"
    assert invalid.value.code == "malformed_xml"


@pytest.mark.parametrize("name", ["../private.xml", "/absolute.xml", "bad\\name.xml"])
def test_unsafe_names_fail_before_filesystem_access(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe names produce one redacted public error contract."""
    with override_settings(HYPERVIEW=FILESYSTEM_SETTINGS.HYPERVIEW):
        resolver = TemplateResolver.from_settings()
        monkeypatch.setattr(
            Path,
            "resolve",
            lambda *args, **kwargs: pytest.fail("unsafe name reached filesystem"),
        )
        with pytest.raises(InvalidTemplateName) as captured:
            resolver.resolve(name)

    assert str(captured.value) == "Invalid template name"
    assert name not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_filesystem_startup_and_render_avoid_optional_infrastructure() -> None:
    """A clean filesystem process renders without database, cache, or network."""
    completed = _run_filesystem_probe()

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "checks": [],
        "rendered": (
            "<view><header>primary-layout</header><text>primary: Clean</text></view>"
        ),
        "sources": 1,
    }
