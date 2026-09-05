import os
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.forms import modelform_factory
from django.test import override_settings

from dj_hyperview.contrib.database.validators import validate_canonical_template_name

ROOT = Path(__file__).parents[1]
UNSAFE_UNICODE_NAMES = (
    "screens/home\n.xml",
    "screens/home\t.xml",
    "screens/\x01home.xml",
    "screens/\x7fhome.xml",
    "screens/\x85home.xml",
    "screens/\ud800.xml",
    "screens/\udfff.xml",
)


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
    assert fields["name"].unique is False
    assert fields["name_identity"].max_length == 64
    assert fields["name_identity"].unique is True
    assert fields["name_identity"].editable is False
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
        *UNSAFE_UNICODE_NAMES,
    ],
)
def test_full_clean_rejects_noncanonical_or_too_long_names(name):
    template = template_model()(name=name, content="<view />")

    with pytest.raises(ValidationError) as captured:
        template.full_clean(validate_unique=False, validate_constraints=False)

    assert "name" in captured.value.error_dict
    if isinstance(name, str) and name:
        assert name not in str(captured.value)


def test_name_validator_drops_invalid_name_exception_context():
    sensitive_name = "../private-screen.xml"

    with pytest.raises(ValidationError) as captured:
        validate_canonical_template_name(sensitive_name)

    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_name not in str(error)
    assert sensitive_name not in repr(error)
    assert sensitive_name not in rendered


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("", "blank"),
        (None, "null"),
        ("<!DOCTYPE view><view />", "forbidden_declaration"),
        (
            "<!ENTITY x SYSTEM 'file:///etc/passwd'><view>&x;</view>",
            "forbidden_declaration",
        ),
        (
            "<!DOCTYPE view SYSTEM 'https://example.invalid/x'><view />",
            "forbidden_declaration",
        ),
        ("<view>\ud800</view>", "malformed_xml"),
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
        "<text>first</text><text>second</text>",
        "Hello {{ user }}",
        "<view {{ attrs }}></view>",
        "<view>",
        "{% load dj_hyperview %}<view><text>unclosed</view>",
    ],
)
def test_full_clean_accepts_safe_documents_partials_and_template_source(content):
    template = template_model()(name="screens/home.xml", content=content)

    template.full_clean(validate_unique=False, validate_constraints=False)

    assert template.content == content


reject_all_schema = Mock(return_value=False)


@override_settings(HYPERVIEW={"VALIDATION": {"SCHEMA": reject_all_schema}})
def test_model_source_validation_does_not_apply_schema_before_render():
    template = template_model()(name="screen.xml", content="<view />")

    template.full_clean(validate_unique=False, validate_constraints=False)

    reject_all_schema.assert_not_called()


@pytest.mark.parametrize("name", ["../screen.xml", "x" * 256, *UNSAFE_UNICODE_NAMES])
def test_clean_fields_rejects_unsafe_name_without_normalizing_it(name):
    template = template_model()(name=name, content="<view />")

    with pytest.raises(ValidationError) as captured:
        template.clean_fields()

    assert "name" in captured.value.error_dict
    assert template.name == name


@pytest.mark.django_db
@pytest.mark.parametrize("revision", [0, -1])
def test_full_clean_reports_nonpositive_revision_on_field(revision):
    template = template_model()(
        name="screen.xml", content="<view />", revision=revision
    )

    with pytest.raises(ValidationError) as captured:
        template.full_clean(validate_unique=False)

    assert set(captured.value.error_dict) == {"revision"}


@pytest.fixture
def template_table(transactional_db):
    model = template_model()
    with connection.schema_editor() as editor:
        editor.create_model(model)
    yield model
    with connection.schema_editor() as editor:
        editor.delete_model(model)


@pytest.mark.parametrize(
    ("exclude", "attributes"),
    [
        ({"content"}, {"name": "screen.xml", "content": "<view>"}),
        ({"name"}, {"name": "../unsafe.xml", "content": "<view />"}),
        ({"name", "content"}, {"name": "../unsafe.xml", "content": "<view>"}),
    ],
)
def test_clean_fields_honors_explicit_exclusions(exclude, attributes):
    template = template_model()(**attributes)

    template.clean_fields(exclude=exclude)

    assert template.name == attributes["name"]
    assert template.content == attributes["content"]


@pytest.mark.parametrize(
    ("fields", "attributes", "data"),
    [
        (["name"], {"content": "<view>"}, {"name": "screen.xml"}),
        (["content"], {"name": "../unsafe.xml"}, {"content": "<view />"}),
        ([], {"name": "../unsafe.xml", "content": "<view>"}, {}),
    ],
)
def test_modelform_ignores_invalid_fields_it_excludes(
    template_table, fields, attributes, data
):
    instance = template_table(**attributes)
    form_class = modelform_factory(template_table, fields=fields)

    form = form_class(data, instance=instance)

    assert form.is_valid(), form.errors.as_data()
    assert form.errors.as_data() == {}


@pytest.mark.parametrize(
    ("exclude", "attributes", "data"),
    [
        (["content"], {"content": "<view>"}, {"name": "screen.xml", "revision": 1}),
        (
            ["name"],
            {"name": "../unsafe.xml"},
            {"content": "<view />", "revision": 1},
        ),
        (
            ["name", "content"],
            {"name": "../unsafe.xml", "content": "<view>"},
            {"revision": 1},
        ),
    ],
)
def test_modelform_exclude_option_skips_stored_invalid_fields(
    template_table, exclude, attributes, data
):
    form_class = modelform_factory(template_table, exclude=exclude)

    form = form_class(data, instance=template_table(**attributes))

    assert form.is_valid(), form.errors.as_data()
    assert form.errors.as_data() == {}


@pytest.mark.parametrize(
    ("fields", "instance", "data", "error_field"),
    [
        (["name"], {"content": "<view />"}, {"name": "bad\n.xml"}, "name"),
        (
            ["content"],
            {"name": "screen.xml"},
            {"content": "<!DOCTYPE view><view />"},
            "content",
        ),
    ],
)
def test_modelform_reports_only_included_invalid_field(
    template_table, fields, instance, data, error_field
):
    form_class = modelform_factory(template_table, fields=fields)

    form = form_class(data, instance=template_table(**instance))

    assert form.is_valid() is False
    assert set(form.errors.as_data()) == {error_field}


def test_deferred_instance_full_clean_remains_valid(template_table):
    row = template_table.objects.create(name="screen.xml", content="<view />")

    deferred = template_table.objects.only("name").get(pk=row.pk)
    deferred.full_clean()

    assert deferred.name == "screen.xml"


def test_sqlite_uniqueness_is_case_sensitive_by_default(template_table):
    template_table.objects.create(name="screen.xml", content="<view />")
    other = template_table(name="Screen.xml", content="<view />")

    other.full_clean()
    other.save()

    assert set(template_table.objects.values_list("name", flat=True)) == {
        "screen.xml",
        "Screen.xml",
    }


def test_database_enforces_unique_name_and_positive_revision(template_table):
    model = template_table
    model.objects.create(name="screen.xml", content="<view />")

    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(name="screen.xml", content="<other />")
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(name="other.xml", content="<view />", revision=0)


def test_save_persists_defaults_and_does_not_call_full_clean(template_table):
    template = template_table(name="screen.xml", content="<view />")

    with patch.object(template, "full_clean") as full_clean:
        template.save()

    full_clean.assert_not_called()
    assert (template.active, template.revision) == (True, 1)
    assert template.created_at is not None
    assert template.updated_at is not None
