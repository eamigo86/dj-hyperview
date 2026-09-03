import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import override_settings

ROOT = Path(__file__).parents[1]


def run_isolated(settings_module, source, *, database=None):
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": settings_module,
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src"))),
    }
    if database is not None:
        environment["DJHV_TEST_DATABASE"] = str(database)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def template_model():
    from dj_hyperview.contrib.database.models import HyperviewTemplate

    return HyperviewTemplate


def test_base_startup_does_not_import_optional_database_models():
    result = run_isolated(
        "tests.settings",
        "import django, sys; django.setup(); "
        "print('dj_hyperview.contrib.database.models' in sys.modules)",
    )

    assert (result.returncode, result.stdout.strip()) == (0, "False")


def test_database_package_import_is_lazy_before_django_setup():
    result = run_isolated(
        "tests.settings",
        "import sys, dj_hyperview.contrib.database; "
        "print('dj_hyperview.contrib.database.models' in sys.modules)",
    )

    assert (result.returncode, result.stdout.strip()) == (0, "False")


def test_contrib_startup_exposes_expected_app_and_model_metadata():
    result = run_isolated(
        "tests.settings_database",
        "import django; django.setup(); from django.apps import apps; "
        "c=apps.get_app_config('dj_hyperview_database'); "
        "m=c.get_model('HyperviewTemplate'); "
        "print(c.name, c.label, c.default_auto_field, m._meta.app_label)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "dj_hyperview.contrib.database dj_hyperview_database "
        "django.db.models.BigAutoField dj_hyperview_database"
    )


def test_template_model_fields_defaults_meta_and_string():
    model = template_model()
    fields = {field.name: field for field in model._meta.fields}
    template = model(name="screens/home.xml", content="<view />")

    assert fields["name"].max_length == 255
    assert fields["name"].unique is True
    assert fields["content"].blank is False
    assert template.active is True
    assert template.revision == 1
    assert model._meta.ordering == ("name",)
    assert model._meta.verbose_name == "Hyperview template"
    assert str(template) == "screens/home.xml"


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        "/absolute.xml",
        "../screen.xml",
        "screens//home.xml",
        "screens\\home.xml",
        "screens/./home.xml",
        "x" * 256,
    ],
)
def test_full_clean_rejects_noncanonical_or_too_long_names(name):
    template = template_model()(name=name, content="<view />")

    with pytest.raises(ValidationError) as captured:
        template.full_clean(validate_unique=False, validate_constraints=False)

    assert "name" in captured.value.error_dict
    if isinstance(name, str) and name:
        assert name not in str(captured.value)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("", "blank"),
        (None, "null"),
        ("<view>", "malformed_xml"),
        ("<!DOCTYPE view><view />", "forbidden_declaration"),
    ],
)
def test_full_clean_rejects_invalid_template_source_without_leaking_it(content, code):
    template = template_model()(name="screen.xml", content=content)

    with pytest.raises(ValidationError) as captured:
        template.full_clean(validate_unique=False, validate_constraints=False)

    assert code in {error.code for error in captured.value.error_dict["content"]}
    if isinstance(content, str) and content:
        assert content not in str(captured.value)


@override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10}})
def test_full_clean_uses_configured_source_size_limit():
    template = template_model()(name="screen.xml", content="<view>secret</view>")

    with pytest.raises(ValidationError) as captured:
        template.full_clean(validate_unique=False, validate_constraints=False)

    assert captured.value.error_dict["content"][0].code == "max_bytes"
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    "content",
    [
        "<view>{{ value }}</view>",
        '{% extends "base.xml" %}{% block body %}<view />{% endblock %}',
    ],
)
def test_full_clean_accepts_safe_static_or_django_template_source(content):
    template = template_model()(name="screens/home.xml", content=content)

    template.full_clean(validate_unique=False, validate_constraints=False)

    assert template.content == content


@pytest.mark.parametrize("name", ["../screen.xml", "x" * 256])
def test_clean_rejects_unsafe_name_without_normalizing_it(name):
    template = template_model()(name=name, content="<view />")

    with pytest.raises(ValidationError) as captured:
        template.clean()

    assert "name" in captured.value.error_dict
    assert template.name == name


@pytest.fixture
def template_table(transactional_db):
    model = template_model()
    with connection.schema_editor() as editor:
        editor.create_model(model)
    yield model
    with connection.schema_editor() as editor:
        editor.delete_model(model)


def test_database_enforces_unique_name_and_positive_revision(template_table):
    model = template_table
    model.objects.create(name="screen.xml", content="<view />")

    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(name="screen.xml", content="<other />")
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(name="other.xml", content="<view />", revision=0)


def test_save_persists_defaults_and_does_not_call_full_clean(template_table):
    template = template_table(name="../unsafe.xml", content="<view />")

    with patch.object(template, "full_clean") as full_clean:
        template.save()

    full_clean.assert_not_called()
    assert (template.active, template.revision) == (True, 1)
    assert template.created_at is not None
    assert template.updated_at is not None
