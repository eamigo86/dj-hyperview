"""Immutable r2 schema corrections and exact completion metadata."""

from copy import deepcopy

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import get_hyperview_catalog

HV = "https://hyperview.org/hyperview"
R2 = "compatible-0.110.0-r2"


@pytest.mark.parametrize(
    "markup",
    [
        '<text ellipsizeMode="tail" accessibilityLabel="Label" a'
        'ccessibilityRole="button" importantForAccessibility="no'
        '">Label</text>',
        '<text ellipsizeMode="clip" accessibilityRole="none"/>',
        '<text ellipsizeMode="head" importantForAccessibility="auto"/>',
        '<text ellipsizeMode="middle" importantForAccessibility="yes"/>',
        '<text importantForAccessibility="no-hide-descendants"/>',
        '<text-field name="title" accessibilityLabel="Title"/>',
        '<date-field name="date" cancel-label="Cancel" done-label="Done"/>',
        '<image source="/avatar.png" style="avatar" alt="Photo" '
        'accessibilityRole="button"/>',
        '<style id="margin" margin="-12.5%"/>',
    ],
)
def test_r2_accepts_only_audited_native_additions(markup):
    with override_settings(HYPERVIEW={}):
        validate_hyperview_schema(markup.replace(" ", f' xmlns="{HV}" ', 1))


@pytest.mark.parametrize(
    "markup",
    [
        '<text ellipsizeMode="invalid"/>',
        '<text accessibilityRole="switch"/>',
        '<image source="/x" style="x" accessibilityRole="none"/>',
        '<text importantForAccessibility="false"/>',
        '<text accessibilityElementsHidden="true"/>',
        '<view accessibilityLabel="Label"/>',
        '<style id="x" letterSpacing="12%"/>',
        '<style id="x" outlineWidth="12%"/>',
        "<doc><styles/><screen><body/></screen></doc>",
    ],
)
def test_r2_preserves_unrelated_rejections(markup):
    from lxml import etree

    node = etree.fromstring(markup)
    node.set("xmlns", HV)
    with override_settings(HYPERVIEW={}):
        with pytest.raises(TemplateValidationError, match=r"\[schema\]"):
            validate_hyperview_schema(etree.tostring(node, encoding="unicode"))


@pytest.mark.parametrize(
    "value", ["0", "84", "12.5", "0.5", "0%", "12.5%", ".5%", "150%"]
)
def test_r2_canonical_width_values(value):
    with override_settings(HYPERVIEW={}):
        validate_hyperview_schema(f'<style xmlns="{HV}" width="{value}"/>')


@pytest.mark.parametrize(
    "value",
    [
        "bananas",
        "NaN",
        "Infinity",
        "auto",
        ".5",
        "12px",
        "1e2",
        "1e2%",
        "12%px",
        "-1",
        "-1%",
        "1%%",
        "",
        "12.",
    ],
)
def test_r2_rejects_noncanonical_width(value):
    with override_settings(HYPERVIEW={}):
        with pytest.raises(TemplateValidationError, match=r"\[schema\]"):
            validate_hyperview_schema(f'<style xmlns="{HV}" width="{value}"/>')


def test_corrected_registry_keeps_legacy_resources_unchanged():
    from lxml import etree

    from dj_hyperview.schema import _compile_schema, get_hyperview_schema_path

    compiled = _compile_schema(get_hyperview_schema_path())
    assert compiled.is_valid(etree.fromstring(f'<style xmlns="{HV}" width="bananas"/>'))
    assert not compiled.is_valid(
        etree.fromstring(f'<text xmlns="{HV}" ellipsizeMode="tail"/>')
    )


def test_r2_catalog_preserves_qualified_attributes_and_is_detached():
    with override_settings(HYPERVIEW={}):
        catalog = get_hyperview_catalog()
        assert catalog["catalog_format"] == 2
        attrs = catalog["elements"]["behavior"]["attributes"]
        assert "message" not in attrs
        assert "{https://hyperview.org/hyperview-alert}message" in attrs
        assert catalog["elements"]["text"]["attributes"]["accessibilityRole"][
            "enum"
        ] == ["button", "none"]
        original = deepcopy(catalog)
        catalog["elements"].clear()
        assert get_hyperview_catalog() == original
