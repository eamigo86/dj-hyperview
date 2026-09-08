"""Opt-in compatibility profiles preserve immutable upstream schema resources."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from django.test import override_settings
from lxml import etree

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import (
    build_hyperview_catalog,
    get_hyperview_catalog,
    get_hyperview_schema_path,
)

HV = "https://hyperview.org/hyperview"
XS = "http://www.w3.org/2001/XMLSchema"
UPSTREAM = "upstream-0.110.0"
COMPATIBLE = "compatible-0.110.0"
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


@pytest.mark.parametrize("profile", [UPSTREAM, COMPATIBLE])
def test_schema_profile_preserves_existing_margin_values(profile: str) -> None:
    """Compatibility is additive: existing integer and auto values still work."""
    with override_settings(HYPERVIEW={"SCHEMA_PROFILE": profile}):
        for value in ("0", "-4", "+3", "auto"):
            for margin in MARGINS:
                validate_hyperview_schema(f'<style xmlns="{HV}" {margin}="{value}"/>')


@pytest.mark.parametrize("profile", [UPSTREAM, COMPATIBLE])
def test_percentage_margins_require_the_explicit_compatible_profile(
    profile: str,
) -> None:
    """Only the selected compatibility profile accepts decimal percentages."""
    with override_settings(HYPERVIEW={"SCHEMA_PROFILE": profile}):
        for value in ("50%", "-12.5%", "+0.5%", ".5%", "-.25%", "100.0%"):
            for margin in MARGINS:
                document = f'<style xmlns="{HV}" {margin}="{value}"/>'
                if profile == COMPATIBLE:
                    validate_hyperview_schema(document)
                else:
                    with pytest.raises(TemplateValidationError, match="\\[schema\\]"):
                        validate_hyperview_schema(document)


@override_settings(HYPERVIEW={"SCHEMA_PROFILE": COMPATIBLE})
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


@override_settings(HYPERVIEW={"SCHEMA_PROFILE": COMPATIBLE})
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


def test_profile_switches_validator_and_catalog_without_mutating_public_builders() -> (
    None
):
    """Runtime profile selection is distinct from immutable upstream inspection."""
    upstream_path = get_hyperview_schema_path()
    upstream_catalog = build_hyperview_catalog()
    document = f'<style xmlns="{HV}" margin="50%"/>'
    for profile in (UPSTREAM, COMPATIBLE, UPSTREAM):
        with override_settings(HYPERVIEW={"SCHEMA_PROFILE": profile}):
            catalog = get_hyperview_catalog()
            assert catalog["schema_profile"] == profile
            assert {
                key: value for key, value in catalog.items() if key != "schema_profile"
            } == upstream_catalog
            assert get_hyperview_schema_path() == upstream_path
            assert build_hyperview_catalog() == upstream_catalog
            if profile == COMPATIBLE:
                validate_hyperview_schema(document)
            else:
                with pytest.raises(TemplateValidationError, match="\\[schema\\]"):
                    validate_hyperview_schema(document)


def test_compatibility_overlay_changes_only_the_nine_margin_types() -> None:
    """The fixed trusted override copies exactly one upstream global element."""
    root = get_hyperview_schema_path().parent
    overlay_path = root / "compatibility" / "hyperview.xsd"
    assert overlay_path.is_file()
    parser = etree.XMLParser(remove_blank_text=True)
    overlay = etree.parse(overlay_path, parser).getroot()
    upstream = etree.parse(root / "core.xsd", parser).getroot()
    override = overlay.find(f"{{{XS}}}override")
    assert override is not None
    assert override.get("schemaLocation") == "../hyperview.xsd"
    assert len(override) == 1
    style = override[0]
    assert style.tag == f"{{{XS}}}element" and style.get("name") == "style"
    original = upstream.find(f"{{{XS}}}element[@name='style']")
    normalized = deepcopy(style)
    changes = []
    for attribute in normalized.iter(f"{{{XS}}}attribute"):
        if attribute.get("type") == "hv:marginSizing":
            changes.append(attribute.get("name"))
            attribute.set("type", "hv:sizing")
    assert set(changes) == set(MARGINS)
    assert len(changes) == 9

    def structure(element):
        return (
            element.tag,
            dict(element.attrib),
            [structure(child) for child in element],
        )

    assert structure(normalized) == structure(original)
    declarations = [(element.tag, element.get("name")) for element in overlay]
    assert declarations == [
        (f"{{{XS}}}override", None),
        (f"{{{XS}}}simpleType", "marginSizing"),
    ]
    union = overlay.find(f"{{{XS}}}simpleType/{{{XS}}}union")
    assert union is not None and union.get("memberTypes") == "hv:sizing"


@pytest.mark.parametrize("profile", [UPSTREAM, COMPATIBLE])
def test_profile_catalog_stays_detached(profile: str) -> None:
    """Caller edits cannot pollute the cached completion metadata."""
    with override_settings(HYPERVIEW={"SCHEMA_PROFILE": profile}):
        catalog = get_hyperview_catalog()
        catalog["elements"].clear()
        catalog["schema_profile"] = "changed"
        assert get_hyperview_catalog()["elements"]
        assert get_hyperview_catalog()["schema_profile"] == profile


@override_settings(HYPERVIEW={"SCHEMA_PROFILE": COMPATIBLE})
def test_compatible_catalog_reuses_packaged_metadata_without_compiling() -> None:
    """Selecting compatible completions must not add runtime XSD compilation."""
    with patch(
        "dj_hyperview.schema._compile_schema",
        side_effect=AssertionError("catalog must use packaged metadata"),
    ):
        assert get_hyperview_catalog()["schema_profile"] == COMPATIBLE


def test_compiled_overlay_catalog_equals_the_packaged_upstream_catalog() -> None:
    """CI proves that sharing immutable completion metadata across profiles is safe."""
    import json

    import xmlschema

    from dj_hyperview.schema import _schema_catalog

    root = get_hyperview_schema_path().parent
    compiled = xmlschema.XMLSchema11(
        root / "compatibility" / "hyperview.xsd", allow="local", use_fallback=False
    )
    assert _schema_catalog(compiled, version="0.110.0") == json.loads(
        (root / "catalog.json").read_text(encoding="utf-8")
    )
