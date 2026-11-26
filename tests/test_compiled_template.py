"""Compiled templates validate their final output exactly once."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import RequestFactory, override_settings

from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, _ValidatedTemplate
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import (
    HyperviewFragmentTemplateResponse,
    HyperviewResponse,
    HyperviewTemplateResponse,
)
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
                    {"screen.xml": '<text xmlns="https://hyperview.org/hyperview" />'},
                )
            ]
        },
    }
]
STUB_SOURCES = [
    {
        "BACKEND": "tests.stubs.TemplateSource",
        "OPTIONS": {"content": '<text xmlns="https://hyperview.org/hyperview" />'},
    }
]


@pytest.fixture
def validation_counts(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count the XML parses and schema validations that happen in a test.

    Args:
        monkeypatch: Wraps the parser and the schema validation with counting spies.

    Returns:
        A dictionary whose parse and schema entries grow with each validation.
    """
    from dj_hyperview import schema, validation

    counts = {"parse": 0, "schema": 0}
    parse = validation._parse
    validate_schema = schema._validate_schema_root

    def counted_parse(*args: Any, **kwargs: Any) -> Any:
        """Count the parse and delegate to the real parser.

        Args:
            *args: Positional arguments of the parser.
            **kwargs: Keyword arguments of the parser.

        Returns:
            The result of the real parser.
        """
        counts["parse"] += 1
        return parse(*args, **kwargs)

    def counted_schema(*args: Any, **kwargs: Any) -> Any:
        """Count the schema validation and delegate to the real one.

        Args:
            *args: Positional arguments of the validation.
            **kwargs: Keyword arguments of the validation.

        Returns:
            The result of the real schema validation.
        """
        counts["schema"] += 1
        return validate_schema(*args, **kwargs)

    monkeypatch.setattr(validation, "_parse", counted_parse)
    monkeypatch.setattr(schema, "_validate_schema_root", counted_schema)
    return counts


def compiled_template(engine: HyperviewEngine, route: str) -> _ValidatedTemplate:
    """Obtain the screen template from the engine by the given route.

    Args:
        engine: Engine that compiles the template.
        route: How the compiled template is obtained: get or select.

    Returns:
        The compiled template for screen.xml.
    """
    if route == "get":
        return engine.get_template("screen.xml")
    return engine.select_template(["missing.xml", "screen.xml"])


def assert_validation_error(code: str, action: Callable[[], object]) -> None:
    """Check that an action fails validation with the given code.

    Args:
        code: Validation error code that is expected.
        action: Function that must raise the validation error.
    """
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == code


@pytest.mark.parametrize("route", ["get", "select"])
def test_compiled_template_render_rejects_malformed_hxml(route: str) -> None:
    """Rendering a compiled template with malformed HXML fails validation.

    Args:
        route: How the compiled template is obtained: get or select.
    """
    engine = HyperviewEngine(
        TemplateResolver(
            [TemplateSource(content='<text xmlns="https://hyperview.org/hyperview">')]
        )
    )

    assert_validation_error(
        code="malformed_xml", action=compiled_template(engine, route).render
    )


@pytest.mark.parametrize("route", ["get", "select"])
@pytest.mark.parametrize(
    ("content", "config", "context", "code"),
    [
        (
            '<text xmlns="https://hyperview.org/hyperview" unknown="x" />',
            ValidationSettings(),
            None,
            "schema",
        ),
        (
            "<v>{{ value }}</v>",
            ValidationSettings(max_bytes=20),
            {"value": "x" * 30},
            "max_bytes",
        ),
    ],
)
def test_compiled_template_enforces_schema_and_limits(
    route: str,
    content: str,
    config: ValidationSettings,
    context: dict[str, str] | None,
    code: str,
) -> None:
    """Rendering enforces the schema and the configured limits.

    Args:
        route: How the compiled template is obtained: get or select.
        content: Template text to render.
        config: Validation settings of the engine.
        context: Render context, or None for none.
        code: Validation error code that is expected.
    """
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content=content)]), validation=config
    )

    assert_validation_error(
        code, lambda: compiled_template(engine, route).render(context)
    )


def test_compiled_template_preserves_django_render_and_metadata() -> None:
    """A compiled template keeps Django rendering behavior and metadata."""
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " value }}</text>"
                    )
                )
            ]
        )
    )
    template = engine.get_template("screen.xml")

    assert template.render({"value": "A & B"}, RequestFactory().get("/")) == (
        '<text xmlns="https://hyperview.org/hyperview">A &amp; B</text>'
    )
    assert template.origin.name == "stub:screen.xml"
    assert template.origin.template_name == "screen.xml"
    assert template.backend is engine.backend
    assert template.template.name == "screen.xml"


@pytest.mark.parametrize("route", ["get", "select", "render"])
def test_compiled_template_validates_exactly_once(
    route: str, validation_counts: dict[str, int]
) -> None:
    """Each route validates the output once, one parse and one schema pass.

    Args:
        route: How the output is produced: get, select or render.
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    engine = HyperviewEngine(
        TemplateResolver(
            [TemplateSource(content='<text xmlns="https://hyperview.org/hyperview" />')]
        ),
    )

    rendered = (
        engine.render("screen.xml")
        if route == "render"
        else compiled_template(engine, route).render()
    )

    assert rendered == '<text xmlns="https://hyperview.org/hyperview" />'
    assert validation_counts == {"parse": 1, "schema": 1}


def test_compiled_template_always_validates_final_output() -> None:
    """The final output is validated even when the source looks fine."""
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>")]),
    )
    assert_validation_error("malformed_xml", engine.get_template("screen.xml").render)


def test_low_level_django_backend_does_not_claim_hyperview_validation() -> None:
    """The bare Django backend renders unvalidated; the engine template validates."""
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>")]),
    )
    assert engine.backend.get_template("screen.xml").render() == "<view>"
    assert_validation_error("malformed_xml", engine.get_template("screen.xml").render)


@pytest.mark.parametrize(
    ("sources", "using"),
    [
        (STUB_SOURCES, None),
        ([], "django"),
        (STUB_SOURCES, "django"),
    ],
)
@pytest.mark.parametrize(
    "response_type", [HyperviewTemplateResponse, HyperviewFragmentTemplateResponse]
)
def test_template_response_validates_once_for_every_engine_selection(
    sources: list[dict[str, object]],
    using: str | None,
    validation_counts: dict[str, int],
    response_type: type[HyperviewTemplateResponse | HyperviewFragmentTemplateResponse],
) -> None:
    """A template response validates once for every engine selection.

    Args:
        sources: Template sources configured for Hyperview.
        using: Engine alias requested by the response, or None.
        validation_counts: Counts the XML parses and schema validations of this test.
        response_type: Template response class under test.
    """
    configured = {
        "SOURCES": sources,
    }

    with override_settings(TEMPLATES=VALID_TEMPLATES, HYPERVIEW=configured):
        response = response_type(
            RequestFactory().get("/screen"), "screen.xml", using=using
        )
        response.render()

    assert response.content == b'<text xmlns="https://hyperview.org/hyperview" />'
    assert validation_counts == {"parse": 1, "schema": 1}


def test_template_response_does_not_revalidate_a_compiled_hyperview_template(
    validation_counts: dict[str, int],
) -> None:
    """A validated compiled template crosses the response boundary once.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    validation = ValidationSettings()
    with override_settings(HYPERVIEW={}):
        engine = HyperviewEngine(
            TemplateResolver(
                [
                    TemplateSource(
                        content='<text xmlns="https://hyperview.org/hyperview" />'
                    )
                ]
            ),
            validation=validation,
        )
        response = HyperviewTemplateResponse(
            RequestFactory().get("/screen"),
            engine.get_template("screen.xml"),
        )
        response.render()

    assert response.content == b'<text xmlns="https://hyperview.org/hyperview" />'
    assert validation_counts == {"parse": 1, "schema": 1}


@pytest.mark.parametrize("marker", ["<!DOCTYPE view", "<!ENTITY xxe"])
def test_processing_instruction_data_is_not_an_active_declaration(marker: str) -> None:
    """Declaration markers inside a processing instruction are plain data.

    Args:
        marker: Declaration text placed inside the processing instruction.
    """
    document = f'<view xmlns="https://hyperview.org/hyperview"><?safe {marker}?></view>'

    assert validate_hxml(document) == document


def test_public_render_then_response_are_independent_validation_operations(
    validation_counts: dict[str, int],
) -> None:
    """Rendering and building a response each validate on their own.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=VALID_TEMPLATES[0]["OPTIONS"]["loaders"][0][1]["screen.xml"]
                )
            ]
        )
    )
    rendered = engine.render("screen.xml")
    assert HyperviewResponse(rendered).content == rendered.encode()
    assert validation_counts == {"parse": 2, "schema": 2}


def test_public_safe_string_is_not_a_validation_capability() -> None:
    """Marking a string safe does not exempt it from validation."""
    from django.utils.safestring import mark_safe

    with pytest.raises(TemplateValidationError):
        HyperviewResponse(
            mark_safe('<view xmlns="https://hyperview.org/hyperview" bogus="x"/>')
        )


def test_rendered_content_stays_fresh_text_without_committing(
    validation_counts: dict[str, int],
) -> None:
    """Reading rendered content validates it without marking the response rendered.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    with override_settings(TEMPLATES=VALID_TEMPLATES, HYPERVIEW={}):
        response = HyperviewTemplateResponse(None, "screen.xml", using="django")
        assert isinstance(response.rendered_content, str)
        assert isinstance(response.rendered_content, str)
        assert response.is_rendered is False
        response.render()
        assert response.render() is response
        assert validation_counts == {"parse": 3, "schema": 3}


def test_lazy_private_handoff_revalidates_when_limits_change(
    monkeypatch: pytest.MonkeyPatch, validation_counts: dict[str, int]
) -> None:
    """A deferred result is validated again when the limits change.

    Args:
        monkeypatch: Replaces the response result with one that changes the limits.
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    with override_settings(TEMPLATES=VALID_TEMPLATES, HYPERVIEW={}):
        response = HyperviewTemplateResponse(None, "screen.xml", using="django")
        original = response._rendered_result
        changed = override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10}})

        def stale_result() -> Any:
            """Produce the result, then change the limits so it becomes stale.

            Returns:
                The result produced before the limits changed.
            """
            result = original()
            changed.enable()
            return result

        monkeypatch.setattr(response, "_rendered_result", stale_result)
        try:
            with pytest.raises(TemplateValidationError) as error:
                response.render()
            assert error.value.code == "max_bytes"
            assert response.is_rendered is False
            assert validation_counts == {"parse": 2, "schema": 1}
        finally:
            changed.disable()


def test_engine_preserves_leading_template_directive_xml_declaration_support() -> None:
    """A template that starts with a directive and an XML declaration still renders."""
    source = (
        '\n<?xml version="1.0" encoding="UTF-8"?><view xml'
        'ns="https://hyperview.org/hyperview"/>'
    )
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=source)]))
    assert engine.render("screen.xml") == source.lstrip()


def test_response_reads_use_cheap_guards_without_revalidating_xml(
    validation_counts: dict[str, int],
) -> None:
    """Reading a response repeatedly never validates the XML again.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    from dj_hyperview import HyperviewResponse

    response = HyperviewResponse('<view xmlns="https://hyperview.org/hyperview"/>')
    for _ in range(2):
        assert response.content
        assert response.text
        assert list(response)
        assert bytes(response)
    assert validation_counts == {"parse": 1, "schema": 1}


def test_gzip_handoff_does_not_repeat_xml_validation(
    validation_counts: dict[str, int],
) -> None:
    """A gzip-compressed response is not validated again.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    import gzip

    response = HyperviewResponse('<view xmlns="https://hyperview.org/hyperview"/>')
    response.content = gzip.compress(response.content)
    response["Content-Encoding"] = "gzip"
    assert bytes(response)
    assert validation_counts == {"parse": 1, "schema": 1}


def test_gzip_revalidates_stale_representation_before_accepting_transport(
    validation_counts: dict[str, int],
) -> None:
    """A stale document is validated again before gzip content is accepted.

    Args:
        validation_counts: Counts the XML parses and schema validations of this test.
    """
    import gzip

    source = '<view xmlns="https://hyperview.org/hyperview"><text>ready</text></view>'
    response = HyperviewResponse(source)
    with override_settings(HYPERVIEW={"VALIDATION": {"MAX_DEPTH": 1}}):
        with pytest.raises(TemplateValidationError) as error:
            response.content = gzip.compress(source.encode())
    assert error.value.code == "max_depth"
    assert response.content == source.encode()
    assert validation_counts == {"parse": 2, "schema": 1}
