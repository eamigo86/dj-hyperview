import pytest
from django.test import RequestFactory, override_settings

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


def assert_validation_error(code, action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == code
    return error.value


def test_validate_hxml_accepts_a_well_formed_document():
    document = "<view xmlns='https://hyperview.org/hyperview'><text>Ready</text></view>"

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


@pytest.mark.parametrize("max_depth", [256])
def test_libxml_depth_ceiling_uses_the_public_max_depth_code(max_depth) -> None:
    """Parser-level depth rejection is reported as the configured limit."""
    document = "<view>" * 257 + "</view>" * 257

    assert_validation_error(
        "max_depth",
        lambda: validate_hxml(document, config=ValidationSettings(max_depth=max_depth)),
    )


def test_parser_depth_wording_variants_keep_the_public_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Libxml message-prefix changes do not alter the stable package code."""

    def reject_depth(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise validation_module.etree.XMLSyntaxError(
            "Maximum DEPTH limit reached", 0, 0, 0
        )

    monkeypatch.setattr(validation_module.etree, "fromstring", reject_depth)

    assert_validation_error("max_depth", lambda: validate_hxml("<view />"))


@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
def test_non_utf8_xml_declarations_are_rejected(validator) -> None:
    """The XML declaration cannot contradict the UTF-8 HTTP contract."""
    document = '<?xml version="1.0" encoding="ISO-8859-1"?><view>café</view>'

    error = assert_validation_error("invalid_encoding", lambda: validator(document))

    assert error.__cause__ is None


def test_render_validation_strips_template_whitespace_before_xml_declaration() -> None:
    """Django load tags may precede a declaration without breaking the response."""
    document = (
        '\n\t<?xml version="1.0" encoding="UTF-8"?>'
        "<view xmlns='https://hyperview.org/hyperview' />"
    )

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


def test_engine_safe_render_escapes_context_and_validates_output():
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        "<text xmlns='https://hyperview.org/hyperview'>"
                        "{{ value }}</text>"
                    )
                )
            ]
        )
    )

    assert engine.render_hxml("screen.xml", {"value": "<&"}) == (
        "<text xmlns='https://hyperview.org/hyperview'>&lt;&amp;</text>"
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


def test_engine_cannot_request_retired_publish_only_mode():
    with pytest.raises(TypeError):
        ValidationSettings(mode="publish")


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
