"""Closed, immutable descriptors for package-generated schema extensions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from lxml import etree

_TYPES = frozenset({"string", "boolean", "integer", "decimal"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")


@dataclass(frozen=True, slots=True)
class _AttributeSpec:
    """One immutable XML lexical type and its optional restrictions."""

    type: Literal["string", "boolean", "integer", "decimal"]
    required: bool = False
    enum: tuple[str, ...] = ()


_Attributes = tuple[tuple[str, _AttributeSpec], ...]


@dataclass(frozen=True, slots=True)
class _SchemaExtensions:
    """Hashable normalized registrations shared by schema and catalog caches."""

    behaviors: tuple[tuple[str, _Attributes], ...] = ()
    element_attributes: tuple[tuple[str, _Attributes], ...] = ()


_EMPTY_EXTENSIONS = _SchemaExtensions()


@lru_cache(maxsize=1)
def _standards() -> dict[str, Any]:
    """Read immutable r2 declarations without loading Django or xmlschema."""
    path = Path(__file__).with_name("schemas") / "0.110.0" / "r2" / "catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))["elements"]


def _valid_name(value: Any) -> bool:
    """Accept literal unqualified XML names safe in generated type selectors."""
    return (
        isinstance(value, str)
        and _NAME.fullmatch(value) is not None
        and not value.lower().startswith("xml")
    )


def _xml_string(value: Any) -> bool:
    """Require a string representable as an XML attribute, including empty text."""
    if not isinstance(value, str):
        return False
    try:
        etree.Element("value").set("value", value)
    except (ValueError, UnicodeError):
        return False
    return True


def _descriptor_errors(value: Any, path: str) -> list[tuple[str, str]]:
    """Validate the closed attribute descriptor without coercing user values.

    Args:
        value: Untrusted configuration descriptor.
        path: Configuration location for actionable errors.

    Returns:
        Configuration path and explanation pairs.
    """
    if not isinstance(value, Mapping):
        return [(path, "must be a typed attribute mapping")]
    errors = []
    if set(value) - {"TYPE", "REQUIRED", "ENUM"}:
        errors.append((path, "contains unsupported descriptor keys"))
    if not isinstance(value.get("TYPE"), str) or value["TYPE"] not in _TYPES:
        errors.append((path + ".TYPE", "must be string, boolean, integer, or decimal"))
    if type(value.get("REQUIRED", False)) is not bool:
        errors.append((path + ".REQUIRED", "must be a boolean"))
    if "ENUM" in value:
        enum = value["ENUM"]
        if (
            value.get("TYPE") != "string"
            or not isinstance(enum, (list, tuple))
            or not enum
            or not all(_xml_string(item) for item in enum)
            or len(set(enum)) != len(enum)
        ):
            errors.append(
                (path + ".ENUM", "must contain unique XML strings for TYPE string")
            )
    return errors


def _attributes_errors(
    value: Any, path: str, standard: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """Reject undeclared descriptors and collisions by expanded attribute name.

    Args:
        value: Raw mapping of additional attributes.
        path: Parent configuration location.
        standard: Qualified and unqualified standard attribute definitions.

    Returns:
        Configuration path and explanation pairs.
    """
    if not isinstance(value, Mapping):
        return [(path, "must be an attribute mapping")]
    errors = []
    for name, descriptor in value.items():
        child = f"{path}.{name}"
        if not _valid_name(name):
            errors.append((child, "must be an unqualified XML attribute name"))
        elif name in standard:
            errors.append((child, "must not redefine a standard attribute"))
        errors.extend(_descriptor_errors(descriptor, child))
    return errors


def _extension_errors(raw: Any) -> list[tuple[str, str]]:
    """Return actionable configuration errors without compiling generated XSD.

    Args:
        raw: Raw extension configuration, before normalization.

    Returns:
        Closed-contract violations as configuration path and message pairs.
    """
    path = "SCHEMA_EXTENSIONS"
    if not isinstance(raw, Mapping):
        return [(path, "must be a mapping")]
    errors = []
    if set(raw) - {"BEHAVIORS", "ELEMENT_ATTRIBUTES"}:
        errors.append((path, "contains unsupported sections"))
    standards = _standards() if raw else {}
    for section in ("BEHAVIORS", "ELEMENT_ATTRIBUTES"):
        entries = raw.get(section, {})
        if not isinstance(entries, Mapping):
            errors.append((f"{path}.{section}", "must be a mapping"))
            continue
        for name, entry in entries.items():
            child = f"{path}.{section}.{name}"
            if section == "BEHAVIORS":
                standard = standards["behavior"]["attributes"]
                if not _valid_name(name) or name in standard["action"]["enum"]:
                    errors.append((child, "must name a new, unqualified custom action"))
                if not isinstance(entry, Mapping) or set(entry) != {"ATTRIBUTES"}:
                    errors.append((child, "must contain only ATTRIBUTES"))
                    continue
                attrs = entry["ATTRIBUTES"]
            else:
                if not _valid_name(name) or name not in standards or name == "behavior":
                    errors.append(
                        (child, "must target a standard element other than behavior")
                    )
                    continue
                standard = standards[name]["attributes"]
                attrs = entry
            errors.extend(_attributes_errors(attrs, child, standard))
    return errors


def _normalize_extensions(raw: Mapping[str, Any]) -> _SchemaExtensions:
    """Detach validated settings into canonical immutable tuples for cache keys.

    Args:
        raw: Extension mapping already accepted by configuration checks.

    Returns:
        Detached registrations with sorted immutable descriptors.
    """

    def attributes(values: Mapping[str, Any]) -> _Attributes:
        """Detach one validated attribute map and sort its enum values."""
        return tuple(
            (
                name,
                _AttributeSpec(
                    value["TYPE"],
                    value.get("REQUIRED", False),
                    tuple(sorted(value.get("ENUM", ()))),
                ),
            )
            for name, value in sorted(values.items())
        )

    return _SchemaExtensions(
        tuple(
            (name, attributes(value["ATTRIBUTES"]))
            for name, value in sorted(raw.get("BEHAVIORS", {}).items())
        ),
        tuple(
            (name, attributes(value))
            for name, value in sorted(raw.get("ELEMENT_ATTRIBUTES", {}).items())
        ),
    )
