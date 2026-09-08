"""Simple-typed custom elements remain strict across registry consumers."""

from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.contrib.database.admin_validation import _static_schema_diagnostics
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import _get_static_validation_catalog, get_hyperview_catalog

APP = "urn:simple-custom-elements"


@pytest.fixture
def simple_schema(tmp_path: Path) -> Path:
    schema = tmp_path / "simple.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        f'xmlns:app="{APP}" targetNamespace="{APP}">'
        '<xs:simpleType name="Label"><xs:restriction base="xs:string">'
        '<xs:enumeration value="Ready"/></xs:restriction></xs:simpleType>'
        '<xs:element name="label" type="app:Label"/>'
        '<xs:element name="count" type="xs:integer"/>'
        "</xs:schema>",
        encoding="utf-8",
    )
    return schema


@pytest.mark.parametrize("consumer", ["completion", "static_catalog", "admin"])
def test_simple_elements_have_no_attributes_or_child_elements(
    simple_schema: Path,
    consumer: str,
) -> None:
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [simple_schema]}):
        if consumer == "admin":
            assert (
                _static_schema_diagnostics(
                    "simple.xml", f'<label xmlns="{APP}">Ready</label>'
                )
                == []
            )
        else:
            catalog = (
                get_hyperview_catalog()
                if consumer == "completion"
                else _get_static_validation_catalog()
            )
            for name in ("label", "count"):
                definition = catalog["elements"][f"{{{APP}}}{name}"]
                assert definition["attributes"] == {}
                if consumer == "completion":
                    assert definition["children"] == []
                    assert definition["allows_custom_children"] is False
                else:
                    assert definition["allows_custom_attributes"] is False


def test_simple_element_runtime_values_and_unknown_attributes_stay_strict(
    simple_schema: Path,
) -> None:
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [simple_schema]}):
        validate_hyperview_schema(f'<label xmlns="{APP}">Ready</label>')
        validate_hyperview_schema(f'<count xmlns="{APP}">12</count>')
        for content in (
            f'<label xmlns="{APP}">Other</label>',
            f'<count xmlns="{APP}">NaN</count>',
            f'<label xmlns="{APP}" unknown="value">Ready</label>',
            f'<label xmlns="{APP}"><count>12</count></label>',
        ):
            with pytest.raises(TemplateValidationError):
                validate_hyperview_schema(content)


def test_simple_element_admin_rejects_unknown_attributes(
    simple_schema: Path,
) -> None:
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [simple_schema]}):
        diagnostics = _static_schema_diagnostics(
            "simple.xml", f'<label xmlns="{APP}" unknown="value">Ready</label>'
        )
        assert [item["code"] for item in diagnostics] == ["schema_attribute"]
