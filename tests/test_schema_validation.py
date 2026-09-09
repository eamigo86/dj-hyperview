"""Tests for automatic Hyperview XSD 1.1 validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings
from lxml import etree

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.conf import ValidationSettings
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.validation import validate_fragment_hxml, validate_hxml

HV = "https://hyperview.org/hyperview"


def test_schema_diagnostics_handle_unqualified_element_names() -> None:
    """Safe diagnostic helpers also support local custom declarations."""
    from dj_hyperview.schema import _schema_local_name

    assert _schema_local_name(etree.Element("custom-component")) == "custom-component"


def test_public_validator_accepts_an_official_hyperview_document() -> None:
    """The public validator uses the bundled Hyperview 0.110.0 schema."""
    document = f'<doc xmlns="{HV}"><screen id="main"><body /></screen></doc>'

    assert validate_hyperview_schema(document) is None


def test_public_validator_enforces_xsd_11_assertions() -> None:
    """XSD assertions prove that validation does not fall back to XSD 1.0."""
    document = f'<nav-route xmlns="{HV}" id="missing-destination" />'

    with pytest.raises(TemplateValidationError) as error:
        validate_hyperview_schema(document)

    assert error.value.code == "schema"
    assert error.value.line == 1
    assert error.value.column is None
    assert str(error.value) == (
        "HXML validation failed [schema] at line 1: document does not match schema"
    )
    assert "missing-destination" not in str(error.value)


def test_schema_error_explains_an_unexpected_child_without_exposing_source() -> None:
    """Structural failures identify the offending and expected element names."""
    document = "\n".join(
        (
            f'<doc xmlns="{HV}">',
            "  <styles />",
            "  <screen><body /></screen>",
            "</doc>",
        )
    )

    with pytest.raises(TemplateValidationError) as error:
        validate_hyperview_schema(document)

    assert error.value.line == 2
    assert str(error.value) == (
        'HXML validation failed [schema] at line 2: element "styles" is not '
        'allowed inside "doc" at this position; expected "screen" or "navigator"'
    )


def test_schema_error_explains_an_incomplete_parent() -> None:
    """Missing child failures name the parent and expected child."""
    document = "\n".join(
        (
            f'<doc xmlns="{HV}">',
            "  <screen>",
            "    <styles />",
            "  </screen>",
            "</doc>",
        )
    )

    with pytest.raises(TemplateValidationError) as error:
        validate_hyperview_schema(document)

    assert error.value.line == 2
    assert str(error.value) == (
        'HXML validation failed [schema] at line 2: element "screen" is '
        'incomplete; expected child "body"'
    )


@pytest.mark.parametrize(
    ("style", "message"),
    [
        (
            '<style id="card" kk="1" />',
            'attribute "kk" is not allowed on element "style"',
        ),
        (
            '<style id="card" width="bananas" />',
            'attribute "width" on element "style" has a value not allowed by the '
            "schema",
        ),
    ],
)
def test_schema_error_explains_invalid_attributes(style: str, message: str) -> None:
    """Attribute failures name the field and element without echoing its value."""
    document = "\n".join(
        (
            f'<doc xmlns="{HV}">',
            "  <screen>",
            "    <styles>",
            f"      {style}",
            "    </styles>",
            "    <body />",
            "  </screen>",
            "</doc>",
        )
    )

    with pytest.raises(TemplateValidationError) as error:
        validate_hyperview_schema(document)

    assert error.value.line == 4
    assert str(error.value) == f"HXML validation failed [schema] at line 4: {message}"
    assert "bananas" not in str(error.value)


def test_schema_error_explains_a_missing_required_attribute() -> None:
    """Required-field failures name the attribute and owning element."""
    document = f'<image xmlns="{HV}" />'

    with pytest.raises(TemplateValidationError) as error:
        validate_hyperview_schema(document)

    assert str(error.value) == (
        'HXML validation failed [schema] at line 1: required attribute "source" '
        'is missing from element "image"'
    )


def test_configured_validator_applies_to_documents_and_fragments() -> None:
    """The same mandatory registry validates both response media contracts."""
    config = ValidationSettings()

    assert validate_hxml(f'<view xmlns="{HV}" />', config=config)
    with pytest.raises(TemplateValidationError) as document_error:
        validate_hxml(f'<unknown xmlns="{HV}" />', config=config)
    with pytest.raises(TemplateValidationError) as fragment_error:
        validate_fragment_hxml(f'<unknown xmlns="{HV}" />', config=config)

    assert document_error.value.code == "schema"
    assert fragment_error.value.code == "schema"


def test_fragment_root_contract_precedes_schema_validation() -> None:
    """A restricted fragment reports its media-contract error before XSD errors."""
    config = ValidationSettings()

    with pytest.raises(TemplateValidationError) as error:
        validate_fragment_hxml(f'<doc xmlns="{HV}" />', config=config)

    assert error.value.code == "restricted_fragment_root"


def test_extra_schema_participates_in_document_validation(tmp_path: Path) -> None:
    """A configured custom namespace is validated when Hyperview allows it."""
    schema = tmp_path / "app.xsd"
    schema.write_text(
        """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
targetNamespace="https://example.test/app" elementFormDefault="qualified">
  <xs:element name="swipe-row">
    <xs:complexType><xs:attribute name="id" use="required"/></xs:complexType>
  </xs:element>
</xs:schema>""",
        encoding="utf-8",
    )
    document = (
        f'<view xmlns="{HV}" xmlns:app="https://example.test/app">'
        "<app:swipe-row />"
        "</view>"
    )

    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        with pytest.raises(TemplateValidationError) as error:
            validate_hyperview_schema(document)

    assert error.value.code == "schema"
    assert error.value.line == 1


def test_compiled_registry_refreshes_when_extra_schema_changes(
    tmp_path: Path,
) -> None:
    """A file fingerprint change cannot reuse a stale compiled schema registry."""
    schema = tmp_path / "app.xsd"
    opening = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="https://example.test/app" elementFormDefault="qualified">'
    )
    schema.write_text(
        opening + '<xs:element name="thing"/></xs:schema>', encoding="utf-8"
    )
    document = (
        f'<view xmlns="{HV}" xmlns:app="https://example.test/app"><app:thing /></view>'
    )
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        validate_hyperview_schema(document)
        schema.write_text(
            opening
            + '<xs:element name="thing"><xs:complexType>'
            + '<xs:attribute name="id" use="required"/>'
            + "</xs:complexType></xs:element></xs:schema>",
            encoding="utf-8",
        )
        with pytest.raises(TemplateValidationError) as error:
            validate_hyperview_schema(document)

    assert error.value.code == "schema"


def test_validation_error_coordinates_remain_backwards_compatible() -> None:
    """Coordinates are optional and do not alter legacy construction."""
    legacy = TemplateValidationError("schema", "invalid")
    located = TemplateValidationError("schema", "invalid", line=4, column=9)

    assert legacy.line is None
    assert legacy.column is None
    assert str(legacy) == "HXML validation failed [schema]: invalid"
    assert str(located) == (
        "HXML validation failed [schema] at line 4, column 9: invalid"
    )
