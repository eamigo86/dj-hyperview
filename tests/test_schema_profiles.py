"""Automatic corrections preserve immutable upstream and historical resources."""

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import (
    build_hyperview_catalog,
    get_hyperview_catalog,
    get_hyperview_schema_path,
)

HV = "https://hyperview.org/hyperview"
MARGINS = (
    "margin",
    "marginBottom",
    "marginHorizontal",
    "marginLeft",
    "marginRight",
    "marginTop",
    "marginEnd",
    "marginStart",
    "marginVertical",
)


def test_corrected_schema_preserves_existing_margin_values() -> None:
    """Compatibility is additive: existing integer and auto values still work."""
    with override_settings(HYPERVIEW={}):
        for value in ("0", "-4", "+3", "auto"):
            for margin in MARGINS:
                validate_hyperview_schema(f'<style xmlns="{HV}" {margin}="{value}"/>')


def test_corrected_schema_accepts_audited_percentage_margins():
    for value in ("50%", "-12.5%", "+0.5%", ".5%", "-.25%", "100.0%"):
        for margin in MARGINS:
            validate_hyperview_schema(f'<style xmlns="{HV}" {margin}="{value}"/>')


@override_settings(HYPERVIEW={})
def test_compatibility_does_not_accept_malformed_margin_percentages() -> None:
    """The overlay does not turn margins into arbitrary strings."""
    for value in (
        "%",
        "1%%",
        "1e2%",
        "NaN%",
        "inf%",
        "1.2.3%",
        "12 px",
        "1.5",
        "prefix50%",
        "50%suffix",
        "1,5%",
    ):
        for margin in MARGINS:
            with pytest.raises(TemplateValidationError, match="\\[schema\\]"):
                validate_hyperview_schema(f'<style xmlns="{HV}" {margin}="{value}"/>')


@override_settings(HYPERVIEW={})
def test_compatibility_does_not_broaden_unrelated_style_attributes() -> None:
    """Shared upstream sizing and numeric types remain untouched."""
    for attribute, value in (
        ("letterSpacing", "50%"),
        ("outlineWidth", "50%"),
        ("flexGrow", "1.5"),
        ("unknownAttribute", "1"),
    ):
        with pytest.raises(TemplateValidationError, match="\\[schema\\]"):
            validate_hyperview_schema(f'<style xmlns="{HV}" {attribute}="{value}"/>')


def test_automatic_catalog_does_not_mutate_upstream_inspection_helpers():
    upstream_path = get_hyperview_schema_path()
    upstream_catalog = build_hyperview_catalog()
    catalog = get_hyperview_catalog()
    assert catalog["catalog_format"] == 2
    assert "schema_profile" not in catalog
    assert get_hyperview_schema_path() == upstream_path
    assert build_hyperview_catalog() == upstream_catalog
    validate_hyperview_schema(f'<style xmlns="{HV}" margin="50%"/>')


def test_active_catalog_stays_detached() -> None:
    """Caller edits cannot pollute the cached completion metadata."""
    with override_settings(HYPERVIEW={}):
        catalog = get_hyperview_catalog()
        catalog["elements"].clear()
        catalog["catalog_format"] = "changed"
        assert get_hyperview_catalog()["elements"]
        assert get_hyperview_catalog()["catalog_format"] == 2


def test_active_catalog_reuses_compiled_registry_for_unchanged_settings():
    from dj_hyperview.schema import _cached_registry

    get_hyperview_catalog()
    hits = _cached_registry.cache_info().hits
    assert get_hyperview_catalog()["catalog_format"] == 2
    assert _cached_registry.cache_info().hits == hits
