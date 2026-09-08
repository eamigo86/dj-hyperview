"""Closed typed schema registrations do not relax unrelated HXML."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError
from dj_hyperview.schema import get_hyperview_catalog

HV = "https://hyperview.org/hyperview"
R2 = "compatible-0.110.0-r2"
EXTENSIONS = {
    "BEHAVIORS": {
        "show-snackbar": {
            "ATTRIBUTES": {
                "message": {"TYPE": "string", "REQUIRED": True},
                "tone": {"TYPE": "string", "ENUM": ["success", "error"]},
            }
        },
        "store-token": {"ATTRIBUTES": {"token": {"TYPE": "string"}}},
    },
    "ELEMENT_ATTRIBUTES": {
        "image": {"variant": {"TYPE": "string", "ENUM": ["face", "fingerprint"]}}
    },
}


def configured(extensions=EXTENSIONS):
    return override_settings(HYPERVIEW={"SCHEMA_EXTENSIONS": extensions})


@pytest.mark.parametrize(
    "attributes",
    [
        'action="show-snackbar" message="Hi" tone="error" trigger="load" once="true"',
        'action="store-token" token=""',
        'action="store-token"',
        'action="show-snackbar" message="Hi" xmlns:a="https://hy'
        'perview.org/hyperview-alert" a:message="Other"',
    ],
)
def test_registered_behavior_attributes_are_action_specific(attributes):
    with configured():
        validate_hyperview_schema(f'<view xmlns="{HV}"><behavior {attributes}/></view>')


@pytest.mark.parametrize(
    "markup",
    [
        '<behavior action="show-snackbar"/>',
        '<behavior action="show-snackbar" message="Hi" tone="bad"/>',
        '<behavior action="show-snackbar" message="Hi" token="bad"/>',
        '<behavior action="reload" message="Hi"/>',
        '<behavior action="unknown" message="Hi"/>',
        '<view action="show-snackbar" message="Hi"/>',
        '<text variant="face"/>',
        '<image style="x" source="/x" variant="invalid"/>',
        '<behavior xmlns:xsi="http://www.w3.org/2001/XMLSchema-i'
        'nstance" xmlns:xs="http://www.w3.org/2001/XMLSchema" xs'
        'i:type="xs:anyType" action="unknown" message="Hi"/>',
    ],
)
def test_registrations_do_not_create_a_wildcard(markup):
    with configured():
        with pytest.raises(TemplateValidationError, match=r"\[schema\]"):
            validate_hyperview_schema(markup.replace(" ", f' xmlns="{HV}" ', 1))


def test_per_element_registration_and_contextual_catalog():
    with configured():
        validate_hyperview_schema(
            f'<image xmlns="{HV}" style="x" source="/x" variant="face"/>'
        )
        catalog = get_hyperview_catalog()
        assert set(catalog["behavior_variants"]) == {"show-snackbar", "store-token"}
        attrs = catalog["behavior_variants"]["show-snackbar"]["attributes"]
        assert attrs["message"]["required"] is True
        assert attrs["tone"]["enum"] == ["error", "success"]
        assert "token" not in attrs
        assert "message" not in catalog["elements"]["behavior"]["attributes"]
        assert (
            "show-snackbar"
            in catalog["elements"]["behavior"]["attributes"]["action"]["enum"]
        )
        assert (
            "show-snackbar"
            not in catalog["elements"]["view"]["attributes"]["action"]["enum"]
        )


@pytest.mark.parametrize(
    "type_name,good,bad",
    [("boolean", "true", "yes"), ("integer", "-2", "1.5"), ("decimal", "1.5", "NaN")],
)
def test_registration_primitive_types(type_name, good, bad):
    ext = {"BEHAVIORS": {"custom": {"ATTRIBUTES": {"value": {"TYPE": type_name}}}}}
    with configured(ext):
        validate_hyperview_schema(
            f'<behavior xmlns="{HV}" action="custom" value="{good}"/>'
        )
        with pytest.raises(TemplateValidationError):
            validate_hyperview_schema(
                f'<behavior xmlns="{HV}" action="custom" value="{bad}"/>'
            )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"UNKNOWN": {}},
        {"BEHAVIORS": []},
        {"ELEMENT_ATTRIBUTES": []},
        {"BEHAVIORS": {"reload": {"ATTRIBUTES": {}}}},
        {"BEHAVIORS": {"bad' or true()": {"ATTRIBUTES": {}}}},
        {"BEHAVIORS": {"custom": {"UNKNOWN": {}}}},
        {"BEHAVIORS": {"custom": {"ATTRIBUTES": {"action": {"TYPE": "string"}}}}},
        {"BEHAVIORS": {"custom": {"ATTRIBUTES": {"href": {"TYPE": "string"}}}}},
        {"ELEMENT_ATTRIBUTES": {"unknown": {"custom": {"TYPE": "string"}}}},
        {"ELEMENT_ATTRIBUTES": {"behavior": {"custom": {"TYPE": "string"}}}},
        {"ELEMENT_ATTRIBUTES": {"image": {"source": {"TYPE": "string"}}}},
        {"ELEMENT_ATTRIBUTES": {"image": {"a:custom": {"TYPE": "string"}}}},
        {"ELEMENT_ATTRIBUTES": {"image": {"xmlns": {"TYPE": "string"}}}},
        {"ELEMENT_ATTRIBUTES": {"image": {"custom": {}}}},
        {"ELEMENT_ATTRIBUTES": {"image": {"custom": {"TYPE": "xpath"}}}},
        {
            "ELEMENT_ATTRIBUTES": {
                "image": {"custom": {"TYPE": "string", "REQUIRED": 1}}
            }
        },
        {
            "ELEMENT_ATTRIBUTES": {
                "image": {"custom": {"TYPE": "boolean", "ENUM": ["true"]}}
            }
        },
        {"ELEMENT_ATTRIBUTES": {"image": {"custom": {"TYPE": "string", "ENUM": []}}}},
        {
            "ELEMENT_ATTRIBUTES": {
                "image": {"custom": {"TYPE": "string", "ENUM": "bad"}}
            }
        },
        {"ELEMENT_ATTRIBUTES": {"image": {"custom": {"TYPE": "string", "ENUM": [1]}}}},
        {
            "ELEMENT_ATTRIBUTES": {
                "image": {"custom": {"TYPE": "string", "ENUM": ["x", "x"]}}
            }
        },
        {
            "ELEMENT_ATTRIBUTES": {
                "image": {"custom": {"TYPE": "string", "XPATH": "true()"}}
            }
        },
    ],
)
def test_invalid_registration_is_a_clear_configuration_error(value):
    with configured(value):
        assert any(
            item.id == "dj_hyperview.E021" for item in check_hyperview_settings()
        )
        with pytest.raises(HyperviewConfigurationError, match="E021"):
            get_settings()


def test_nonempty_registration_uses_automatic_registry():
    with override_settings(HYPERVIEW={"SCHEMA_EXTENSIONS": EXTENSIONS}):
        assert not any(
            item.id == "dj_hyperview.E021" for item in check_hyperview_settings()
        )


def test_normalized_registry_is_immutable_and_settings_changes_recompile():
    with configured(deepcopy(EXTENSIONS)):
        normalized = get_settings().schema_extensions
        with pytest.raises((FrozenInstanceError, AttributeError)):
            normalized.behaviors = ()
        validate_hyperview_schema(f'<behavior xmlns="{HV}" action="store-token"/>')
        with configured({}):
            with pytest.raises(TemplateValidationError):
                validate_hyperview_schema(
                    f'<behavior xmlns="{HV}" action="store-token"/>'
                )
        validate_hyperview_schema(f'<behavior xmlns="{HV}" action="store-token"/>')


def test_r2_custom_catalog_before_any_document_validation(tmp_path):
    from dj_hyperview.schema import _get_static_validation_catalog

    schema = tmp_path / "app.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="https://example.test/app" elementFormDefault="qualified">'
        '<xs:element name="thing"><xs:complexType>'
        '<xs:attribute name="label" use="required"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    with override_settings(
        HYPERVIEW={
            "EXTRA_SCHEMAS": [schema],
            "SCHEMA_EXTENSIONS": EXTENSIONS,
        }
    ):
        key = "{https://example.test/app}thing"
        assert get_hyperview_catalog()["elements"][key]["attributes"]["label"][
            "required"
        ]
        assert _get_static_validation_catalog()["elements"][key]["attributes"]["label"][
            "required"
        ]


def test_doc_extension_preserves_existing_namespaced_wildcard():
    with configured({"ELEMENT_ATTRIBUTES": {"doc": {"app-label": {"TYPE": "string"}}}}):
        validate_hyperview_schema(
            f'<doc xmlns="{HV}" app-label="label"><screen><body/></screen></doc>'
        )
        from dj_hyperview.schema import _get_static_validation_catalog

        definition = _get_static_validation_catalog()["elements"]["doc"]
        assert definition["allows_custom_attributes"]
        assert "app-label" in definition["attributes"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        [],
        {"TYPE": []},
        {"TYPE": "string", "ENUM": ["\x00"]},
        {"TYPE": "string", "ENUM": ["\ud800"]},
    ],
)
def test_invalid_descriptor_values_fail_closed(value):
    with configured({"ELEMENT_ATTRIBUTES": {"image": {"variant": value}}}):
        with pytest.raises(HyperviewConfigurationError, match="E021"):
            get_settings()


def test_r2_catalog_dependency_identity_and_guards(tmp_path):
    from tests.test_schema_dependency_safety import APP, schema_text, write_graph

    root, leaf = write_graph(tmp_path)
    settings = {
        "EXTRA_SCHEMAS": [root],
        "SCHEMA_EXTENSIONS": EXTENSIONS,
    }
    document = f'<view xmlns="{HV}" xmlns:app="{APP}"><app:thing/></view>'
    with override_settings(HYPERVIEW=settings):
        first = get_hyperview_catalog()
        validate_hyperview_schema(document)
        leaf.write_text(
            schema_text(
                '<xs:element name="thing"><xs:complexType><xs:attribute '
                'name="needed" use="required"/></xs:complexType></xs:ele'
                "ment>"
            )
        )
        assert get_hyperview_catalog() != first
        with pytest.raises(TemplateValidationError):
            validate_hyperview_schema(document)
        leaf.write_text(schema_text("<xs:override/>"))
        for operation in (
            get_hyperview_catalog,
            lambda: validate_hyperview_schema(document),
        ):
            with pytest.raises(
                TemplateValidationError, match="forbidden_schema_reference"
            ):
                operation()


def test_registered_xsi_type_cannot_override_a_different_action():
    with configured():
        document = (
            f'<behavior xmlns="{HV}" xmlns:hv="{HV}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="hv:_djhvRegisteredBehavior0" action="reload" message="Hi"/>'
        )
        with pytest.raises(TemplateValidationError):
            validate_hyperview_schema(document)


def test_static_catalog_and_type_hook_select_exact_behavior_contract():
    from lxml import etree

    from dj_hyperview.schema import (
        _get_compiled_registry,
        _get_declaration_type,
        _get_static_validation_catalog,
    )

    with configured():
        compiled = _get_compiled_registry()
        element = etree.fromstring(f'<behavior xmlns="{HV}" action="show-snackbar"/>')
        selected = _get_declaration_type(compiled.maps.elements[element.tag], element)
        assert selected.attributes["message"].use == "required"
        assert not selected.attributes["tone"].type.is_valid("bad")
        static = _get_static_validation_catalog()
        assert (
            "show-snackbar"
            not in static["elements"]["behavior"]["attributes"]["action"]["enum"]
        )
        assert static["behavior_variants"]["show-snackbar"]["attributes"]["message"][
            "required"
        ]


def test_raw_settings_mutations_cannot_change_the_snapshot():
    raw = deepcopy(EXTENSIONS)
    with configured(raw):
        before = get_settings().schema_extensions
        raw["BEHAVIORS"]["show-snackbar"]["ATTRIBUTES"]["tone"]["ENUM"].append(
            "changed"
        )
        assert get_settings().schema_extensions == before
        assert (
            "changed"
            not in get_hyperview_catalog()["behavior_variants"]["show-snackbar"][
                "attributes"
            ]["tone"]["enum"]
        )


def test_duplicate_extra_elements_with_different_children_are_rejected(tmp_path):
    paths = []
    for child in ("first", "second"):
        path = tmp_path / f"{child}.xsd"
        path.write_text(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="https://example.test/duplicate" '
            'elementFormDefault="qualified"><xs:element name="thing">'
            f'<xs:complexType><xs:sequence><xs:element name="{child}"/>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
        paths.append(path)
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": paths}):
        with pytest.raises(
            TemplateValidationError, match="duplicate_schema_declaration"
        ):
            get_hyperview_catalog()


@pytest.mark.parametrize("operation", ["catalog", "validation", "static"])
def test_duplicate_extra_attribute_types_fail_in_every_consumer(tmp_path, operation):
    from dj_hyperview.schema import _get_static_validation_catalog

    paths = []
    for type_name in ("string", "integer"):
        path = tmp_path / f"{type_name}.xsd"
        path.write_text(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="https://example.test/duplicate" '
            'elementFormDefault="qualified"><xs:element name="thing">'
            '<xs:complexType><xs:attribute name="value" '
            f'type="xs:{type_name}"/></xs:complexType></xs:element></xs:schema>'
        )
        paths.append(path)
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": paths}):
        operations = {
            "catalog": get_hyperview_catalog,
            "static": _get_static_validation_catalog,
            "validation": lambda: validate_hyperview_schema(f'<view xmlns="{HV}"/>'),
        }
        with pytest.raises(
            TemplateValidationError, match="duplicate_schema_declaration"
        ):
            operations[operation]()


def test_extra_alternative_without_default_preserves_its_declared_fallback(tmp_path):
    from dj_hyperview.schema import _get_static_validation_catalog

    path = tmp_path / "alternative.xsd"
    path.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:app="https://example.test/alternative" '
        'targetNamespace="https://example.test/alternative">'
        '<xs:complexType name="Special"><xs:attribute name="kind"/></xs:complexType>'
        '<xs:element name="thing"><xs:alternative test="@kind" type="app:Special"/>'
        "</xs:element></xs:schema>"
    )
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [path]}):
        definition = _get_static_validation_catalog()["elements"][
            "{https://example.test/alternative}thing"
        ]
        assert definition["allows_custom_attributes"] is True
