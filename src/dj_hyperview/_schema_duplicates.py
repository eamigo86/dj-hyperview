"""Fail closed when registry roots disagree on a global XSD declaration."""

from __future__ import annotations

import re
from typing import Any

from .exceptions import TemplateValidationError

_XS = "http://www.w3.org/2001/XMLSchema"
_MAPS = ("elements", "types", "attributes", "attribute_groups", "groups", "notations")
_QNAME_ATTRIBUTES = frozenset(
    {
        "ref",
        "type",
        "base",
        "itemType",
        "substitutionGroup",
        "memberTypes",
        "refer",
        "notQName",
    }
)


def _expanded_tokens(value: str, namespaces: dict[str, str]) -> str:
    """Resolve QName lists using the namespace scope of their XML node.

    Args:
        value: Whitespace-separated QName tokens or wildcard keywords.
        namespaces: Actual in-scope namespace bindings.

    Returns:
        Expanded names, preserving special wildcard tokens.
    """
    tokens = []
    for token in value.split():
        if token.startswith("##"):
            tokens.append(token)
            continue
        prefix, separator, local = token.partition(":")
        uri = namespaces.get(prefix if separator else "", "")
        tokens.append(f"{{{uri}}}{local if separator else token}")
    return " ".join(tokens)


def _effective_attributes(
    node: Any, schema: Any, namespaces: dict[str, str], *, global_node: bool
) -> dict[str, Any]:
    """Include inherited defaults only where a declaration can use them.

    Args:
        node: Declaration or nested XSD node.
        schema: Schema resource that owns this declaration.
        namespaces: Namespace bindings at this node, not flattened schema aliases.
        global_node: Whether this node is a global declaration.

    Returns:
        Normalized attributes including effective form and derivation defaults.
    """
    attributes = dict(node.attrib)
    kind = node.tag.removeprefix(f"{{{_XS}}}")
    if not global_node and kind in {"attribute", "element"} and "name" in attributes:
        attributes.setdefault("form", getattr(schema, f"{kind}_form_default"))
    if kind in {"element", "complexType", "simpleType"}:
        allowed = {"extension", "restriction"}
        if kind == "simpleType":
            allowed = {"restriction", "list", "union"}
        final = attributes.get("final", schema.final_default)
        attributes["final"] = tuple(
            sorted(allowed if final == "#all" else allowed.intersection(final.split()))
        )
        if kind != "simpleType":
            if kind == "element":
                allowed.add("substitution")
            block = attributes.get("block", schema.block_default)
            attributes["block"] = tuple(
                sorted(
                    allowed if block == "#all" else allowed.intersection(block.split())
                )
            )
    if kind == "complexType":
        apply = attributes.pop("defaultAttributesApply", "true").strip() not in {
            "false",
            "0",
        }
        group = schema.default_attributes if apply else None
        attributes["$defaultAttributes"] = None if group is None else group.name
    if kind in {"assert", "assertion", "alternative", "selector", "field"}:
        default = attributes.pop(
            "xpathDefaultNamespace", schema.xpath_default_namespace
        )
        default = {
            "##local": "",
            "##targetNamespace": schema.target_namespace,
            "##defaultNamespace": namespaces.get("", ""),
        }.get(default, default)
        attributes["$xpathDefaultNamespace"] = default
        expression = attributes.get("test", attributes.get("xpath", ""))
        prefixes = set(re.findall(r"(?<![\w.-])([\w.-]+):(?!:)", expression))
        attributes["$xpathPrefixes"] = tuple(
            sorted((prefix, namespaces.get(prefix, "")) for prefix in prefixes)
        )
    for name in attributes.keys() & _QNAME_ATTRIBUTES:
        attributes[name] = _expanded_tokens(attributes[name], namespaces)
    return attributes


def _declaration_signature(
    node: Any,
    schema: Any,
    components: dict[Any, Any],
    namespaces: dict[str, str],
    *,
    global_node: bool = False,
) -> tuple[Any, ...]:
    """Compare declaration structure together with its effective schema context.

    Args:
        node: XML node defining a compiled component or a nested declaration.
        schema: Resource owning the declaration and inherited defaults.
        components: Compiled subcomponents indexed by their source XML nodes.
        namespaces: Namespace bindings inherited from the containing XML node.
        global_node: Whether this is the global declaration being compared.

    Returns:
        Structural identity excluding annotations and indentation, including
        relevant defaults, node-scoped QName references and effective open content.
    """
    namespaces = schema.source.get_nsmap(node) or namespaces
    attributes = _effective_attributes(
        node, schema, namespaces, global_node=global_node
    )
    component = components.get(node)
    open_content = getattr(component, "open_content", None)
    if node.tag == f"{{{_XS}}}complexType" and open_content is not None:
        attributes["$openContent"] = _declaration_signature(
            open_content.elem, open_content.schema, {}, open_content.namespaces
        )
    return (
        node.tag,
        tuple(sorted(attributes.items())),
        (node.text or "").strip(),
        tuple(
            _declaration_signature(child, schema, components, namespaces)
            for child in node
            if isinstance(child.tag, str) and child.tag != f"{{{_XS}}}annotation"
        ),
    )


def _guard_duplicate_declarations(compiled: Any, extra_schemas: list[Any]) -> None:
    """Reject disagreement before runtime, static validation, or catalog reuse.

    Args:
        compiled: Selected package registry with all configured schema locations.
        extra_schemas: Independently compiled, already guarded local roots.

    Raises:
        TemplateValidationError: If two roots disagree on a global declaration.
    """
    known: dict[tuple[str, str], tuple[Any, ...]] = {}
    for schema in [compiled, *extra_schemas]:
        for category in _MAPS:
            for name, component in getattr(schema.maps, category).items():
                if name.startswith(f"{{{_XS}}}"):
                    continue
                signature = _declaration_signature(
                    component.elem,
                    component.schema,
                    {
                        item.elem: item
                        for item in component.iter_components()
                        if hasattr(item, "open_content")
                    },
                    component.schema.source.get_nsmap(component.schema.root) or {},
                    global_node=True,
                )
                key = category, name
                if key in known and known[key] != signature:
                    raise TemplateValidationError(
                        "duplicate_schema_declaration",
                        "schema contains an incompatible duplicate declaration",
                    )
                known[key] = signature
