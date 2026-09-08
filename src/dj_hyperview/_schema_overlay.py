"""Generate trusted, closed XSD alternatives from normalized configuration."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import etree

from ._schema_extensions import _Attributes, _SchemaExtensions

_XS = "http://www.w3.org/2001/XMLSchema"
_HV = "https://hyperview.org/hyperview"
_ALERT = "https://hyperview.org/hyperview-alert"
_SCROLL = "https://hyperview.org/hyperview-scroll"


def _add(parent: etree._Element, tag: str, **attributes: str) -> etree._Element:
    """Append a schema node with safely escaped configuration attribute values."""
    return etree.SubElement(parent, f"{{{_XS}}}{tag}", **attributes)


def _append_attributes(parent: etree._Element, attributes: _Attributes) -> None:
    """Append explicit typed attributes before any wildcard or assertion.

    Args:
        parent: Trusted complex type to extend in memory.
        attributes: Checked immutable attribute declarations.
    """
    for name, descriptor in attributes:
        element = etree.Element(f"{{{_XS}}}attribute", name=name)
        if descriptor.required:
            element.set("use", "required")
        if descriptor.enum:
            restriction = _add(
                _add(element, "simpleType"), "restriction", base="xs:string"
            )
            for value in descriptor.enum:
                _add(restriction, "enumeration", value=value)
        else:
            element.set("type", f"xs:{descriptor.type}")
        position = next(
            (
                index
                for index, child in enumerate(parent)
                if child.tag in {f"{{{_XS}}}anyAttribute", f"{{{_XS}}}assert"}
            ),
            len(parent),
        )
        parent.insert(position, element)


def _generated_overlay(
    schema_path: Path, extensions: _SchemaExtensions
) -> etree._Element:
    """Create a trusted in-memory overlay; never accept caller-provided XSD.

    Args:
        schema_path: Fixed bundled r2 entry point.
        extensions: Checked immutable additions with no standard collisions.

    Returns:
        Schema tree whose references all target fixed package resources.
    """
    schema_root = schema_path.parent.parent
    core = etree.parse(schema_root / "core.xsd").getroot()
    revision = etree.parse(schema_path).getroot()
    result = etree.Element(
        f"{{{_XS}}}schema",
        nsmap={"xs": _XS, "hv": _HV, "alert": _ALERT, "scroll": _SCROLL},
        targetNamespace=_HV,
        elementFormDefault="qualified",
    )
    for namespace, filename in ((_ALERT, "alert.xsd"), (_SCROLL, "scroll.xsd")):
        _add(
            result,
            "import",
            namespace=namespace,
            schemaLocation=str(schema_root / filename),
        )
    override = _add(result, "override", schemaLocation=str(schema_path))
    for name, attributes in extensions.element_attributes:
        declaration = revision.find(
            f"{{{_XS}}}override/{{{_XS}}}element[@name='{name}']"
        )
        if declaration is None:
            declaration = core.find(f"{{{_XS}}}element[@name='{name}']")
        element = deepcopy(declaration)
        _append_attributes(element.find(f"{{{_XS}}}complexType"), attributes)
        override.append(element)
    if extensions.behaviors:
        behavior = _add(override, "element", name="behavior")
        group = _add(result, "attributeGroup", name="_djhvStandardBehaviorAttributes")
        for attribute in core.find(
            f"{{{_XS}}}attributeGroup[@name='behaviorAttributes']"
        ):
            if attribute.get("name") != "action":
                group.append(deepcopy(attribute))
        sequence = core.find(
            f"{{{_XS}}}complexType[@name='behavior']/{{{_XS}}}sequence"
        )
        for index, (action, attributes) in enumerate(extensions.behaviors):
            type_name = f"_djhvRegisteredBehavior{index}"
            _add(
                behavior,
                "alternative",
                test=f"@action = '{action}'",
                type=f"hv:{type_name}",
            )
            element_type = _add(result, "complexType", name=type_name)
            element_type.append(deepcopy(sequence))
            _add(
                element_type, "attributeGroup", ref="hv:_djhvStandardBehaviorAttributes"
            )
            action_attribute = _add(
                element_type, "attribute", name="action", use="required"
            )
            restriction = _add(
                _add(action_attribute, "simpleType"), "restriction", base="xs:string"
            )
            _add(restriction, "enumeration", value=action)
            _append_attributes(element_type, attributes)
        _add(behavior, "alternative", type="hv:behavior")
    return result
