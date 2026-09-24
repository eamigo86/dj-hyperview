"""The active 0.111.0 contract extends, but never rewrites, the 0.110.0 archive."""

import hashlib
import json
from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError
from dj_hyperview.schema import (
    _compile_schema,
    _get_registry_snapshot,
    _schema_catalog,
    get_hyperview_catalog,
    get_hyperview_schema_path,
)

HV = "https://hyperview.org/hyperview"
SCHEMAS = Path(__file__).resolve().parents[1] / "src/dj_hyperview/schemas"


@pytest.mark.parametrize("element", ["view", "list", "section-list"])
@pytest.mark.parametrize("value", ["true", "false", "1", "0"])
def test_active_schema_accepts_optional_content_insets(element, value):
    with override_settings(HYPERVIEW={}):
        validate_hyperview_schema(
            f'<{element} xmlns="{HV}" content-insets="{value}">' + f"</{element}>"
        )


@pytest.mark.parametrize(
    "markup",
    [
        '<view content-insets="yes"/>',
        '<screen content-insets="true"><body/></screen>',
        '<list content-insets="sometimes"/>',
        '<text content-insets="true"/>',
        '<image source="/a" content-insets="true"/>',
        '<view content-inset="true"/>',
    ],
)
def test_content_insets_does_not_relax_other_attributes(markup):
    with override_settings(HYPERVIEW={}):
        with pytest.raises(TemplateValidationError, match=r"\[schema\]"):
            validate_hyperview_schema(markup.replace(" ", f' xmlns="{HV}" ', 1))


def test_active_catalog_and_extension_collision_use_new_corrected_contract():
    with override_settings(HYPERVIEW={}):
        revision, _registry = _get_registry_snapshot()
        catalog = get_hyperview_catalog()
    assert "0.111.0" in revision[0]
    assert catalog["schema_version"] == "0.111.0"
    scroll_elements = {
        "body",
        "form",
        "header",
        "item",
        "items",
        "list",
        "section-list",
        "section-title",
        "view",
    }
    assert {
        name
        for name, definition in catalog["elements"].items()
        if "content-insets" in definition["attributes"]
    } == scroll_elements
    for name in scroll_elements:
        assert catalog["elements"][name]["attributes"]["content-insets"] == {
            "enum": [],
            "required": False,
        }
    assert "content-insets" not in catalog["elements"]["text"]["attributes"]

    with override_settings(
        HYPERVIEW={
            "SCHEMA_EXTENSIONS": {
                "ELEMENT_ATTRIBUTES": {"view": {"content-insets": {"TYPE": "boolean"}}}
            }
        }
    ):
        with pytest.raises(HyperviewConfigurationError, match="content-insets"):
            get_settings()


def test_upstream_0111_resources_have_published_checksums_and_legacy_is_stable():
    current = SCHEMAS / "0.111.0"
    legacy = get_hyperview_schema_path().parent
    assert legacy == SCHEMAS / "0.110.0"
    manifest = json.loads((current / "PROVENANCE.json").read_text())
    assert manifest["version"] == "0.111.0"
    assert manifest["tarball_sha256"] == (
        "17f643bf0a587d9b3c91d368738168f0f8a5e8c5add07bed983c2d93dc8e0333"
    )
    for filename, digest in manifest["schema_sha256"].items():
        assert hashlib.sha256((current / filename).read_bytes()).hexdigest() == digest
    assert hashlib.sha256((legacy / "core.xsd").read_bytes()).hexdigest() == (
        "fb5e6594acb7f8d54589f9528caca7902b8fef6447cc7d362ec6cd575a2bf9c3"
    )


def test_corrected_r3_catalog_and_manifest_match_the_active_validator():
    current = SCHEMAS / "0.111.0"
    revision = current / "r3"
    manifest = json.loads((revision / "manifest.json").read_text())
    assert manifest["upstream_version"] == "0.111.0"
    assert manifest["schema_profile"] == "compatible-0.111.0-r3"
    for filename, digest in manifest["sha256"].items():
        assert hashlib.sha256((revision / filename).read_bytes()).hexdigest() == digest
    compiled = _compile_schema(revision / "hyperview.xsd")
    generated = _schema_catalog(compiled, version="0.111.0", qualified=True)
    assert generated == json.loads((revision / "catalog.json").read_text())
    with override_settings(HYPERVIEW={}):
        assert get_hyperview_catalog() == generated
