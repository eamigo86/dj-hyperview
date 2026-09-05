from concurrent.futures import ThreadPoolExecutor
from time import sleep

import pytest
from django.test import RequestFactory, override_settings
from django.test.signals import setting_changed

import dj_hyperview.validation as validation_module
from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import (
    _contains_forbidden_declaration,
    validate_hxml,
    validate_rendered_hxml,
    validate_template_source,
)

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


def test_declaration_scan_has_a_constant_time_common_path() -> None:
    """Documents without declaration literals avoid the block-aware scan."""

    class TrackedDocument(str):
        startswith_calls = 0

        def startswith(self, *args, **kwargs):
            self.startswith_calls += 1
            return super().startswith(*args, **kwargs)

    document = TrackedDocument(f"<view>{'content' * 10_000}</view>")

    assert _contains_forbidden_declaration(document) is False
    assert document.startswith_calls == 0


@pytest.mark.parametrize("max_depth", [256, 1_000])
def test_libxml_depth_ceiling_uses_the_public_max_depth_code(max_depth) -> None:
    """Parser-level depth rejection is reported as the configured limit."""
    document = "<view>" * 257 + "</view>" * 257

    assert_validation_error(
        "max_depth",
        lambda: validate_hxml(document, config=ValidationSettings(max_depth=max_depth)),
    )


@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
def test_non_utf8_xml_declarations_are_rejected(validator) -> None:
    """The XML declaration cannot contradict the UTF-8 HTTP contract."""
    document = '<?xml version="1.0" encoding="ISO-8859-1"?><view>café</view>'

    error = assert_validation_error("invalid_encoding", lambda: validator(document))

    assert error.__cause__ is None


def test_render_validation_strips_template_whitespace_before_xml_declaration() -> None:
    """Django load tags may precede a declaration without breaking the response."""
    document = '\n\t<?xml version="1.0" encoding="UTF-8"?><view />'

    assert validate_rendered_hxml(document).startswith("<?xml")


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


def test_schema_size_is_independent_from_the_document_byte_limit(tmp_path) -> None:
    """A strict screen limit does not reject a larger valid schema."""
    schema = tmp_path / "screen.xsd"
    padded = XSD.replace(
        '<xs:element name="view"',
        f'<!-- {"padding" * 100} -->\n<xs:element name="view"',
    )
    schema.write_text(padded, encoding="utf-8")
    document = "<view>ok</view>"

    assert (
        validate_hxml(
            document, config=ValidationSettings(schema=schema, max_bytes=len(document))
        )
        == document
    )


def test_schema_compilation_is_cached_and_cleared_on_setting_change(
    tmp_path, monkeypatch
) -> None:
    """Repeated documents reuse XSD compilation until configuration changes."""
    schema = tmp_path / "screen.xsd"
    schema.write_text(XSD, encoding="utf-8")
    original = validation_module.etree.XMLSchema
    compile_calls = 0

    def counting_compiler(root):
        nonlocal compile_calls
        compile_calls += 1
        return original(root)

    monkeypatch.setattr(validation_module.etree, "XMLSchema", counting_compiler)
    config = ValidationSettings(schema=schema)

    validate_hxml("<view>first</view>", config=config)
    validate_hxml("<view>second</view>", config=config)
    assert compile_calls == 1

    setting_changed.send(sender=object, setting="HYPERVIEW", value={}, enter=True)
    validate_hxml("<view>third</view>", config=config)
    assert compile_calls == 2


def test_schema_compilation_is_single_flight_across_threads(
    tmp_path, monkeypatch
) -> None:
    """Concurrent first use compiles one validator for a schema revision."""
    schema = tmp_path / "concurrent.xsd"
    schema.write_text(XSD, encoding="utf-8")
    original = validation_module.etree.XMLSchema
    compile_calls = 0

    def slow_compiler(root):
        nonlocal compile_calls
        compile_calls += 1
        sleep(0.01)
        return original(root)

    monkeypatch.setattr(validation_module.etree, "XMLSchema", slow_compiler)
    config = ValidationSettings(schema=schema)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda value: validate_hxml(f"<view>{value}</view>", config=config),
                range(8),
            )
        )

    assert len(results) == 8
    assert compile_calls == 1


def test_schema_cache_refreshes_after_a_file_revision_change(tmp_path) -> None:
    """A new schema fingerprint cannot reuse the previous compiled validator."""
    schema = tmp_path / "mutable.xsd"
    schema.write_text(XSD, encoding="utf-8")
    config = ValidationSettings(schema=schema)
    validate_hxml("<view>first</view>", config=config)

    schema.write_text(XSD.replace('name="view"', 'name="screen"'), encoding="utf-8")

    assert validate_hxml("<screen>second</screen>", config=config) == (
        "<screen>second</screen>"
    )


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
    assert dj_hyperview.validate_template_source is validate_template_source
    assert dj_hyperview.validate_hxml is validate_hxml
