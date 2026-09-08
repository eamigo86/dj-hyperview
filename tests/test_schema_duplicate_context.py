"""Duplicate schema identity includes inherited declaration context."""

from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import _get_static_validation_catalog, get_hyperview_catalog

XS = "http://www.w3.org/2001/XMLSchema"
APP = "urn:r2-duplicate-default"
HV = "https://hyperview.org/hyperview"
R2 = "compatible-0.110.0-r2"


def _schema(body: str, **attributes: str) -> str:
    values = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    return (
        f'<xs:schema xmlns:xs="{XS}" xmlns:app="{APP}" xmlns:t="{XS}" '
        f'targetNamespace="{APP}" {values}>{body}</xs:schema>'
    )


def _context_pair(context: str) -> tuple[str, str]:
    if context == "attributeFormDefault":
        body = (
            '<xs:element name="thing"><xs:complexType>'
            '<xs:attribute name="value" type="xs:integer" use="required"/>'
            "</xs:complexType></xs:element>"
        )
        return tuple(
            _schema(body, attributeFormDefault=form)
            for form in ("unqualified", "qualified")
        )
    if context == "elementFormDefault":
        body = (
            '<xs:element name="thing"><xs:complexType><xs:sequence>'
            '<xs:element name="value" type="xs:integer"/>'
            "</xs:sequence></xs:complexType></xs:element>"
        )
        return tuple(
            _schema(body, elementFormDefault=form)
            for form in ("unqualified", "qualified")
        )
    if context == "defaultAttributes":
        body = (
            '<xs:attributeGroup name="defaults"><xs:attribute name="value" '
            'type="xs:integer" use="required"/></xs:attributeGroup>'
            '<xs:element name="thing"><xs:complexType/></xs:element>'
        )
        return _schema(body), _schema(body, defaultAttributes="app:defaults")
    if context == "namespaceRebinding":
        body = (
            '<xs:simpleType name="integer"><xs:restriction base="xs:string"/>'
            '</xs:simpleType><xs:element name="thing"><xs:complexType>'
            '<xs:attribute name="value" type="t:integer" use="required" '
            'xmlns:t="REPLACE"/></xs:complexType></xs:element>'
        )
        return _schema(body.replace("REPLACE", XS)), _schema(
            body.replace("REPLACE", APP)
        )
    if context in ("blockDefault", "finalDefault"):
        body = '<xs:element name="thing" type="xs:string"/>'
        return _schema(body), _schema(body, **{context: "restriction"})
    if context == "defaultOpenContent":
        body = (
            '<xs:element name="thing"><xs:complexType>'
            '<xs:attribute name="value" type="xs:string"/>'
            "</xs:complexType></xs:element>"
        )
        open_content = (
            '<xs:defaultOpenContent appliesToEmpty="true">'
            '<xs:any namespace="##other" processContents="lax"/>'
            "</xs:defaultOpenContent>"
        )
        return _schema(body), _schema(open_content + body)
    if context == "xpathPrefixRebinding":
        body = (
            '<xs:element name="thing"><xs:complexType><xs:sequence>'
            '<xs:element name="value" type="xs:integer" form="qualified"/>'
            '</xs:sequence><xs:assert xmlns:p="REPLACE" test="exists(p:value)"/>'
            "</xs:complexType></xs:element>"
        )
        return _schema(body.replace("REPLACE", APP)), _schema(
            body.replace("REPLACE", "urn:other")
        )
    body = (
        '<xs:element name="thing"><xs:complexType><xs:sequence>'
        '<xs:element name="value" type="xs:integer" form="qualified"/>'
        '</xs:sequence><xs:assert test="exists(value)"/>'
        "</xs:complexType></xs:element>"
    )
    return _schema(body), _schema(body, xpathDefaultNamespace="##targetNamespace")


def _run(operation: str) -> None:
    if operation == "runtime":
        validate_hyperview_schema(f'<view xmlns="{HV}"/>')
    elif operation == "static":
        _get_static_validation_catalog()
    else:
        get_hyperview_catalog()


@pytest.mark.parametrize(
    "context",
    [
        "attributeFormDefault",
        "elementFormDefault",
        "defaultAttributes",
        "namespaceRebinding",
        "xpathDefaultNamespace",
        "blockDefault",
        "finalDefault",
        "defaultOpenContent",
        "xpathPrefixRebinding",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operation", ["runtime", "static", "catalog"])
def test_conflicting_context_is_rejected_before_any_registry_consumer(
    tmp_path: Path,
    context: str,
    reverse: bool,
    operation: str,
) -> None:
    paths = []
    for index, text in enumerate(_context_pair(context)):
        path = tmp_path / f"root{index}.xsd"
        path.write_text(text)
        paths.append(path)
    if reverse:
        paths.reverse()
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": paths}):
        with pytest.raises(
            TemplateValidationError, match="duplicate_schema_declaration"
        ):
            _run(operation)


@pytest.mark.parametrize("explicit_form", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_explicit_and_implicit_forms_remain_accepted(
    tmp_path: Path,
    explicit_form: bool,
    reverse: bool,
) -> None:
    forms = ' form="unqualified"' if explicit_form else ""
    body = (
        '<xs:element name="thing"><xs:complexType><xs:sequence>'
        f'<xs:element name="child" type="xs:string"{forms}/>'
        f'</xs:sequence><xs:attribute name="value" type="xs:integer"{forms}/>'
        "</xs:complexType></xs:element>"
    )
    first = _schema(body)
    second = _schema(
        body,
        attributeFormDefault="qualified" if explicit_form else "unqualified",
        elementFormDefault="qualified" if explicit_form else "unqualified",
    )
    paths = [tmp_path / "first.xsd", tmp_path / "second.xsd"]
    for path, text in zip(paths, (first, second), strict=True):
        path.write_text(text)
    if reverse:
        paths.reverse()
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": paths}):
        for operation in ("runtime", "static", "catalog"):
            _run(operation)


def _equivalent_pair(context: str) -> tuple[str, str]:
    if context == "defaultAttributesApply":
        body = (
            '<xs:attributeGroup name="defaults"><xs:attribute name="value" '
            'type="xs:integer"/></xs:attributeGroup><xs:element name="thing">'
            '<xs:complexType defaultAttributesApply="false"/></xs:element>'
        )
        return _schema(body), _schema(body, defaultAttributes="app:defaults")
    if context == "explicitDerivation":
        body = (
            '<xs:complexType name="ThingType" block="" final=""/>'
            '<xs:element name="thing" type="app:ThingType" block="" final=""/>'
        )
        return _schema(body), _schema(
            body, blockDefault="restriction", finalDefault="restriction"
        )
    if context == "equivalentDerivation":
        body = '<xs:element name="thing"><xs:complexType/></xs:element>'
        return (
            _schema(body, blockDefault="#all", finalDefault="#all"),
            _schema(
                body,
                blockDefault="substitution extension restriction",
                finalDefault="restriction extension",
            ),
        )
    if context == "defaultOpenContentNotApplicable":
        body = '<xs:element name="thing"><xs:complexType/></xs:element>'
        open_content = (
            '<xs:defaultOpenContent><xs:any namespace="##other" '
            'processContents="lax"/></xs:defaultOpenContent>'
        )
        return _schema(body), _schema(open_content + body)
    if context == "explicitOpenContent":
        body = (
            '<xs:element name="thing"><xs:complexType><xs:openContent mode="none"/>'
            "</xs:complexType></xs:element>"
        )
        open_content = (
            '<xs:defaultOpenContent appliesToEmpty="true"><xs:any '
            'namespace="##other" processContents="lax"/></xs:defaultOpenContent>'
        )
        return _schema(body), _schema(open_content + body)
    if context == "namespaceAlias":
        body = (
            '<xs:element name="thing"><xs:complexType><xs:attribute '
            'name="value" type="t:string"/></xs:complexType></xs:element>'
        )
        return _schema(body), _schema(
            body.replace('type="t:string"', 'type="xs:string"')
        )
    if context == "wildcardKeyword":
        body = (
            '<xs:element name="thing"><xs:complexType><xs:sequence><xs:any '
            'namespace="##other" notQName="##defined" processContents="lax"/>'
            "</xs:sequence></xs:complexType></xs:element>"
        )
        return _schema(body), _schema(body, attributeFormDefault="qualified")
    body = (
        '<xs:element name="thing"><xs:complexType><xs:sequence>'
        '<xs:element name="value" type="xs:integer" form="unqualified"/>'
        '</xs:sequence><xs:assert test="exists(value)" '
        'xpathDefaultNamespace="##local"/></xs:complexType></xs:element>'
    )
    if context == "unusedBinding":
        return _schema(body, **{"xmlns:unused": APP}), _schema(
            body, **{"xmlns:unused": "urn:other"}
        )
    return _schema(body), _schema(body, xpathDefaultNamespace="##targetNamespace")


@pytest.mark.parametrize(
    "context",
    [
        "defaultAttributesApply",
        "explicitDerivation",
        "equivalentDerivation",
        "defaultOpenContentNotApplicable",
        "explicitOpenContent",
        "namespaceAlias",
        "wildcardKeyword",
        "unusedBinding",
        "explicitXPath",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_context_is_not_rejected(
    tmp_path: Path,
    context: str,
    reverse: bool,
) -> None:
    paths = [tmp_path / "first.xsd", tmp_path / "second.xsd"]
    for path, text in zip(paths, _equivalent_pair(context), strict=True):
        path.write_text(text)
    if reverse:
        paths.reverse()
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": paths}):
        for operation in ("runtime", "static", "catalog"):
            _run(operation)
