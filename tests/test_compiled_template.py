import pytest
from django.test import RequestFactory, override_settings

from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import validate_hxml

from .stubs import TemplateSource

VALID_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {"screen.xml": "<view />"},
                )
            ]
        },
    }
]
STUB_SOURCES = [
    {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"content": "<view />"}}
]


class SchemaCounter:
    def __init__(self):
        self.calls = 0

    def __call__(self, document):
        self.calls += 1


def compiled_template(engine, route):
    if route == "get":
        return engine.get_template("screen.xml")
    return engine.select_template(["missing.xml", "screen.xml"])


def assert_validation_error(code, action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == code


@pytest.mark.parametrize("route", ["get", "select"])
def test_compiled_template_render_rejects_malformed_hxml(route):
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content="<view>")]))

    assert_validation_error(
        code="malformed_xml", action=compiled_template(engine, route).render
    )


@pytest.mark.parametrize("route", ["get", "select"])
@pytest.mark.parametrize(
    ("content", "config", "context", "code"),
    [
        ("<view />", ValidationSettings(schema=lambda document: False), None, "schema"),
        (
            "<v>{{ value }}</v>",
            ValidationSettings(max_bytes=20),
            {"value": "x" * 30},
            "max_bytes",
        ),
    ],
)
def test_compiled_template_enforces_schema_and_limits(
    route, content, config, context, code
):
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content=content)]), validation=config
    )

    assert_validation_error(
        code, lambda: compiled_template(engine, route).render(context)
    )


def test_compiled_template_preserves_django_render_and_metadata():
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>{{ value }}</view>")])
    )
    template = engine.get_template("screen.xml")

    assert template.render({"value": "A & B"}, RequestFactory().get("/")) == (
        "<view>A &amp; B</view>"
    )
    assert template.origin.name == "stub:screen.xml"
    assert template.origin.template_name == "screen.xml"
    assert template.backend is engine.backend
    assert template.template.name == "screen.xml"


@pytest.mark.parametrize("route", ["get", "select", "render"])
def test_compiled_template_validates_exactly_once(route):
    schema = SchemaCounter()
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view />")]),
        validation=ValidationSettings(schema=schema),
    )

    rendered = (
        engine.render("screen.xml")
        if route == "render"
        else compiled_template(engine, route).render()
    )

    assert rendered == "<view />"
    assert schema.calls == 1


@pytest.mark.parametrize("mode", ["render", "publish_and_render"])
def test_compiled_template_honors_runtime_validation_modes(mode):
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>")]),
        validation=ValidationSettings(mode=mode),
    )

    assert_validation_error("malformed_xml", engine.get_template("screen.xml").render)


def test_compiled_template_honors_publish_only_mode():
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>")]),
        validation=ValidationSettings(mode="publish"),
    )

    assert engine.get_template("screen.xml").render() == "<view>"


@pytest.mark.parametrize(
    ("sources", "using"),
    [
        (STUB_SOURCES, None),
        ([], None),
        (STUB_SOURCES, "django"),
    ],
)
def test_template_response_validates_once_for_every_engine_selection(sources, using):
    schema = SchemaCounter()
    configured = {
        "SOURCES": sources,
        "VALIDATION": {"SCHEMA": schema},
    }

    with override_settings(TEMPLATES=VALID_TEMPLATES, HYPERVIEW=configured):
        response = HyperviewTemplateResponse(
            RequestFactory().get("/screen"), "screen.xml", using=using
        )
        response.render()

    assert response.content == b"<view />"
    assert schema.calls == 1


@pytest.mark.parametrize("marker", ["<!DOCTYPE view", "<!ENTITY xxe"])
def test_processing_instruction_data_is_not_an_active_declaration(marker):
    document = f"<view><?safe {marker}?></view>"

    assert validate_hxml(document) == document
