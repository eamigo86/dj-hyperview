"""Contract tests for the bundled Hyperview schema registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import (
    HYPERVIEW_SCHEMA_VERSION,
    build_hyperview_catalog,
    get_hyperview_catalog,
    get_hyperview_schema_path,
)

EXPECTED_CHECKSUMS = {
    "alert.xsd": "3d3d951a7349eba2da068133934ad00c3ad86483441a442e9b4a64353fd532a4",
    "core.xsd": "fb5e6594acb7f8d54589f9528caca7902b8fef6447cc7d362ec6cd575a2bf9c3",
    "hyperview.xsd": "f4074a715b7fe4c3ce38396b7ae7bdfa03692ab9138c7d1afb8ae218fa39089a",
    "scroll.xsd": "f67ff1f62ba86aacd3b2d7d10d5bd9952972f6e55dd7200a5100f6012ced8c2d",
}


def test_registry_bundles_the_exact_hyperview_0110_schemas() -> None:
    """The registry carries immutable upstream resources with known checksums."""
    root = get_hyperview_schema_path().parent

    assert HYPERVIEW_SCHEMA_VERSION == "0.110.0"
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.glob("*.xsd")
    } == EXPECTED_CHECKSUMS
    assert (root / "PROVENANCE.md").is_file()


def test_generated_catalog_is_deterministic_and_current() -> None:
    """Committed editor metadata must match deterministic XSD introspection."""
    generated = build_hyperview_catalog()
    committed_path = get_hyperview_schema_path().with_name("catalog.json")

    assert json.loads(committed_path.read_text(encoding="utf-8")) == generated
    assert generated["schema_version"] == "0.110.0"
    assert generated["elements"]["view"]["namespace"] == (
        "https://hyperview.org/hyperview"
    )
    assert "text" in generated["elements"]["view"]["children"]
    assert "id" in generated["elements"]["view"]["attributes"]
    assert "push" in generated["elements"]["behavior"]["attributes"]["action"]["enum"]


def test_custom_catalog_merges_local_namespaced_elements(tmp_path: Path) -> None:
    """Project schemas extend completions without replacing official declarations."""
    schema = tmp_path / "components.xsd"
    schema.write_text(
        """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
targetNamespace="https://example.test/app" elementFormDefault="qualified">
  <xs:element name="swipe-row">
    <xs:complexType>
      <xs:attribute name="threshold" use="required">
        <xs:simpleType><xs:restriction base="xs:string">
          <xs:enumeration value="short"/><xs:enumeration value="long"/>
        </xs:restriction></xs:simpleType>
      </xs:attribute>
    </xs:complexType>
  </xs:element>
</xs:schema>""",
        encoding="utf-8",
    )

    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        catalog = get_hyperview_catalog()

    element = catalog["elements"]["{https://example.test/app}swipe-row"]
    assert element["attributes"]["threshold"] == {
        "enum": ["long", "short"],
        "required": True,
    }


@pytest.mark.parametrize(
    "location",
    ["https://example.test/remote.xsd", "../outside.xsd"],
)
def test_custom_catalog_rejects_unsafe_schema_references(
    tmp_path: Path, location: str
) -> None:
    """Custom schemas cannot fetch remotely or escape their local directory."""
    schema = tmp_path / "components.xsd"
    schema.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        f'<xs:include schemaLocation="{location}"/>'
        "</xs:schema>",
        encoding="utf-8",
    )

    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [schema]}):
        with pytest.raises(TemplateValidationError) as error:
            get_hyperview_catalog()

    assert error.value.code == "forbidden_schema_reference"
