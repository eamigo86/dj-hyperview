import os
import subprocess
import sys
from pathlib import Path

import django
from django.core.checks import run_checks
from django.test import override_settings

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


@override_settings(HYPERVIEW="invalid")
def test_app_config_registers_hyperview_checks() -> None:
    errors = run_checks(tags=["dj_hyperview"])

    assert [error.id for error in errors] == ["dj_hyperview.E001"]


@override_settings(HYPERVIEW={})
def test_registered_check_reports_minimal_empty_resolver() -> None:
    messages = run_checks(tags=["dj_hyperview"])

    assert [message.id for message in messages] == ["dj_hyperview.W005"]


def test_importing_the_package_does_not_load_django_test() -> None:
    """Production processes must not pay for Django's test framework."""
    program = """
import sys

import django
from django.conf import settings

settings.configure(INSTALLED_APPS=[], SECRET_KEY="test-only-key")
django.setup()


def loaded():
    return {name for name in sys.modules if name.split(".")[:2] == ["django", "test"]}


before = loaded()
import dj_hyperview, dj_hyperview.apps, dj_hyperview.checks, dj_hyperview.conf
import dj_hyperview.engine, dj_hyperview.schema, dj_hyperview.validators
import dj_hyperview.templatetags.dj_hyperview
print(django.get_version())
print(",".join(sorted(loaded() - before)))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={
            **os.environ,
            # Prepend, so a Django supplied through PYTHONPATH is still imported.
            "PYTHONPATH": os.pathsep.join(
                filter(None, [str(SOURCE_ROOT), os.environ.get("PYTHONPATH")])
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    # The child must run the Django under test, also when PYTHONPATH supplies it.
    assert result.stdout.splitlines() == [django.get_version(), ""]
