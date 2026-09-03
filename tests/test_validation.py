import pytest
from django.test import RequestFactory, override_settings

from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import validate_hxml

from .stubs import TemplateSource

XSD = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
<xs:element name="view" type="xs:string" />
</xs:schema>"""


def assert_validation_error(code, action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == code
    return error.value


def test_validate_hxml_accepts_a_well_formed_document():
    document = "<view><text>Ready</text></view>"

    assert validate_hxml(document) == document


def test_validate_hxml_rejects_malformed_xml_with_a_stable_error():
    error = assert_validation_error("malformed_xml", lambda: validate_hxml("<view>"))

    assert str(error) == "HXML validation failed [malformed_xml]: invalid XML"


@pytest.mark.parametrize(
    "document",
    [
        "<!DOCTYPE view><view />",
        '<!DOCTYPE view [<!ENTITY xxe SYSTEM "http://127.0.0.1/secret">]>'
        "<view>&xxe;</view>",
        '<!DOCTYPE view [<!ENTITY a "1234567890"><!ENTITY b "&a;&a;">]>'
        "<view>&b;</view>",
    ],
)
def test_validate_hxml_denies_dtd_external_and_expanding_entities(document):
    error = assert_validation_error(
        "forbidden_declaration", lambda: validate_hxml(document)
    )

    assert error.message == "DTD and entity declarations are forbidden"


@pytest.mark.parametrize(
    ("document", "config", "code"),
    [
        ("<view>é</view>", ValidationSettings(max_bytes=14), "max_bytes"),
        (
            "<view><body><text /></body></view>",
            ValidationSettings(max_depth=2),
            "max_depth",
        ),
        ("<view><text /><text /></view>", ValidationSettings(max_nodes=2), "max_nodes"),
    ],
)
def test_validate_hxml_enforces_configured_limits(document, config, code):
    assert_validation_error(code, lambda: validate_hxml(document, config=config))


def test_validate_hxml_applies_a_consumer_xsd(tmp_path):
    schema = tmp_path / "screen.xsd"
    schema.write_text(XSD, encoding="utf-8")
    config = ValidationSettings(schema=schema)

    assert validate_hxml("<view>Ready</view>", config=config) == "<view>Ready</view>"
    error = assert_validation_error(
        "schema", lambda: validate_hxml("<screen />", config=config)
    )
    assert error.message == "document does not match schema"


@pytest.mark.parametrize("location", ["../outside.xsd", "https://example.com/a.xsd"])
def test_validate_hxml_denies_external_schema_references(tmp_path, location):
    schema = tmp_path / "screen.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        f'<xs:include schemaLocation="{location}" />'
        "</xs:schema>",
        encoding="utf-8",
    )

    assert_validation_error(
        "forbidden_schema_reference",
        lambda: validate_hxml("<view />", config=ValidationSettings(schema=schema)),
    )


@pytest.mark.parametrize(
    ("schema", "expected_code"),
    [
        (int, "schema"),
        (lambda document: False, "schema"),
        ("missing.schema", "schema_invalid"),
    ],
)
def test_validate_hxml_normalizes_other_schema_failures(schema, expected_code):
    assert_validation_error(
        expected_code,
        lambda: validate_hxml("<view />", config=ValidationSettings(schema=schema)),
    )


def test_validate_hxml_accepts_a_dotted_callable_schema():
    document = "<view />"
    config = ValidationSettings(schema="tests.stubs.validate_schema")

    assert validate_hxml(document, config=config) == document


@pytest.mark.parametrize(
    "schema_content",
    [
        "<xs:schema>",
        "<view />",
        '<!DOCTYPE schema [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><schema />',
    ],
)
def test_validate_hxml_rejects_invalid_or_hostile_schemas(tmp_path, schema_content):
    schema = tmp_path / "screen.xsd"
    schema.write_text(schema_content, encoding="utf-8")

    expected = (
        "forbidden_declaration" if "DOCTYPE" in schema_content else "schema_invalid"
    )
    assert_validation_error(
        expected,
        lambda: validate_hxml("<view />", config=ValidationSettings(schema=schema)),
    )


def test_engine_safe_render_escapes_context_and_validates_output():
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>{{ value }}</view>")])
    )

    assert engine.render_hxml("screen.xml", {"value": "<&"}) == (
        "<view>&lt;&amp;</view>"
    )


@pytest.mark.parametrize(
    ("content", "config", "context", "code"),
    [
        (
            "<!DOCTYPE view><view>{% unknown %}</view>",
            ValidationSettings(),
            None,
            "forbidden_declaration",
        ),
        (
            "<view>{{ value }}</view>",
            ValidationSettings(max_bytes=30),
            {"value": "x" * 40},
            "max_bytes",
        ),
        ("<view />", ValidationSettings(max_bytes=1), None, "max_bytes"),
    ],
)
def test_engine_rejects_unsafe_source_or_rendered_output(
    content, config, context, code
):
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content=content)]), validation=config
    )

    assert_validation_error(code, lambda: engine.render_hxml("screen.xml", context))


def test_engine_publish_only_mode_skips_post_render_validation():
    engine = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<view>")]),
        validation=ValidationSettings(mode="publish"),
    )

    assert engine.render_hxml("screen.xml") == "<view>"


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": "<view>{{ value|safe }}</view>"},
            }
        ]
    }
)
def test_public_render_and_response_reject_malformed_post_render_output():
    direct_error = assert_validation_error(
        "malformed_xml",
        lambda: render_template("screen.xml", {"value": "<broken>"}),
    )

    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"),
        "screen.xml",
        {"value": "<broken>"},
    )
    response_error = assert_validation_error("malformed_xml", response.render)

    assert direct_error.message == "invalid XML"
    assert response_error.message == "invalid XML"


def test_validation_contract_is_exported_from_package_root():
    import dj_hyperview

    assert dj_hyperview.TemplateValidationError is TemplateValidationError
    assert dj_hyperview.validate_hxml is validate_hxml
