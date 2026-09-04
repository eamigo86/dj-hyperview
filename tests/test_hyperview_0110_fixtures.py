"""Offline Hyperview 0.110.0 synthetic contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from lxml import etree
from tools.package_guard import validate_project

PROJECT_ROOT = Path(__file__).parents[1]
CONTRACT = Path(__file__).parent / "contracts" / "hyperview" / "0.110.0"
NAMESPACE = "https://hyperview.org/hyperview"
NAMESPACES = {"hv": NAMESPACE}
FIXTURES = ("full.xml", "fragment.xml", "form.xml")
NPM_INTEGRITY = (
    "sha512-Yj2VDMEkOtqVA01XAAg93fR/Kx5SUzxQUaaDG004B8Oc+DZO"
    "LVrLw4UJxZ7Gn6Mpc90Upor6wUEBRX+yKDZt1Q=="
)


def _manifest() -> dict[str, Any]:
    return json.loads((CONTRACT / "manifest.json").read_text(encoding="utf-8"))


def _parse(source: bytes | Path) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    content = source.read_bytes() if isinstance(source, Path) else source
    return etree.fromstring(content, parser=parser)


def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(_parse(CONTRACT / "focused-hyperview.xsd"))


def test_contract_manifest_pins_official_hyperview_0110_provenance() -> None:
    """Pin the synthetic contract to verified official release metadata."""
    manifest = _manifest()

    assert manifest["contract"] == "focused-test-only"
    assert manifest["complete"] is False
    assert manifest["hyperview_version"] == "0.110.0"
    assert manifest["audited_at"] == "2026-09-04"
    assert manifest["namespace"] == NAMESPACE
    assert manifest["sources"] == {
        "npm": {
            "package": "hyperview",
            "published_at": "2026-08-19T15:56:02.351Z",
            "tarball": "https://registry.npmjs.org/hyperview/-/hyperview-0.110.0.tgz",
            "integrity": NPM_INTEGRITY,
            "shasum": "a363b7b9d75efa6f91c85a9b9f89ee74b7c967ba",
        },
        "repository": {
            "url": "https://github.com/Instawork/hyperview",
            "tag": "v0.110.0",
            "tag_object": "9c436e788c85c0d9b8b33310a36cc32983342504",
            "commit": "f715ae5cdf07733a4b846d7744518e42dff40407",
        },
        "documentation": "https://hyperview.org/docs/guide_html",
    }


def test_manifest_digests_cover_every_contract_artifact() -> None:
    """Detect any unaudited change to the schema or fixtures."""
    manifest = _manifest()
    expected_files = {"focused-hyperview.xsd", *FIXTURES}

    assert set(manifest["sha256"]) == expected_files
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("name", FIXTURES)
def test_synthetic_fixture_parses_and_validates_offline(name: str) -> None:
    """Validate each committed fixture without network access."""
    document = _parse(CONTRACT / name)
    schema = _schema()

    assert schema.validate(document), schema.error_log
    assert document.nsmap[None] == NAMESPACE


def test_full_fixture_has_document_screen_and_body_shape() -> None:
    """Keep full documents distinct from partial update roots."""
    document = _parse(CONTRACT / "full.xml")

    assert document.tag == f"{{{NAMESPACE}}}doc"
    assert len(document.xpath("./hv:screen/hv:body", namespaces=NAMESPACES)) == 1
    assert document.xpath("string(.//hv:text)", namespaces=NAMESPACES) == "Hello"


def test_fragment_and_form_are_independent_partial_roots() -> None:
    """Keep fragment and form fixtures valid as independent roots."""
    fragment = _parse(CONTRACT / "fragment.xml")
    form = _parse(CONTRACT / "form.xml")

    assert fragment.tag == f"{{{NAMESPACE}}}view"
    assert form.tag == f"{{{NAMESPACE}}}form"
    assert fragment.get("id") == "status-fragment"
    assert form.xpath("string(./hv:text-field/@name)", namespaces=NAMESPACES) == "email"


def test_fixture_references_resolve_to_unique_ids() -> None:
    """Require unique identifiers and resolvable update targets."""
    form = _parse(CONTRACT / "form.xml")
    ids = form.xpath("//@id")
    targets = form.xpath("//@target")

    assert len(ids) == len(set(ids)) == 4
    assert targets == ["form-result"]
    assert set(targets) <= set(ids)


@pytest.mark.parametrize(
    "invalid",
    [
        b'<view xmlns="https://example.invalid"><text>No</text></view>',
        f'<doc xmlns="{NAMESPACE}"><screen /></doc>'.encode(),
        f'<form xmlns="{NAMESPACE}"><text-field /></form>'.encode(),
        f'<view xmlns="{NAMESPACE}"><unknown /></view>'.encode(),
    ],
)
def test_focused_schema_rejects_contract_mutations(invalid: bytes) -> None:
    """Reject namespace, shape, attribute, and element mutations."""
    assert not _schema().validate(_parse(invalid))


def test_contract_is_test_only_and_runtime_package_has_no_markup() -> None:
    """Keep the compatibility corpus outside the runtime package."""
    artifacts = tuple(CONTRACT.iterdir())

    assert {path.suffix for path in artifacts} == {".json", ".xml", ".xsd"}
    assert all(path.is_relative_to(PROJECT_ROOT / "tests") for path in artifacts)
    assert validate_project(PROJECT_ROOT) == []


def test_parser_rejects_malformed_contract_input() -> None:
    """Reject malformed XML before focused schema validation."""
    with pytest.raises(etree.XMLSyntaxError):
        _parse(f'<view xmlns="{NAMESPACE}">'.encode())
