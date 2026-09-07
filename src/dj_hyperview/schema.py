"""Hyperview XSD resources and deterministic editor catalog generation."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.dispatch import receiver
from django.test.signals import setting_changed
from lxml import etree

from .conf import get_settings
from .exceptions import TemplateValidationError

HYPERVIEW_SCHEMA_VERSION = "0.110.0"
HYPERVIEW_NAMESPACE = "https://hyperview.org/hyperview"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema"
_SCHEMA_ROOT = Path(__file__).with_name("schemas") / HYPERVIEW_SCHEMA_VERSION
_REFERENCE_TAGS = frozenset(
    {
        f"{{{XSD_NAMESPACE}}}include",
        f"{{{XSD_NAMESPACE}}}import",
        f"{{{XSD_NAMESPACE}}}redefine",
    }
)


def get_hyperview_schema_path() -> Path:
    """Return the bundled root schema path.

    Returns:
        Absolute path to the versioned Hyperview schema entry point.
    """
    return _SCHEMA_ROOT / "hyperview.xsd"


def _xmlschema_module() -> Any:
    try:
        import xmlschema
    except ImportError as error:
        raise TemplateValidationError(
            "schema_dependency", "XSD validation requires the schema extra"
        ) from error
    return xmlschema


def _attribute_definition(attribute: Any) -> dict[str, Any]:
    values = sorted(str(value) for value in (attribute.type.enumeration or ()))
    return {"enum": values, "required": attribute.use == "required"}


def _catalog_key(name: str, namespace: str) -> str:
    local = name.rsplit("}", 1)[-1]
    return local if namespace == HYPERVIEW_NAMESPACE else f"{{{namespace}}}{local}"


def _schema_catalog(schema: Any, *, version: str | None = None) -> dict[str, Any]:
    elements: dict[str, Any] = {}
    declarations = sorted(
        schema.maps.elements.values(), key=lambda item: item.name or ""
    )
    for element in declarations:
        if not element.name or element.target_namespace == XSD_NAMESPACE:
            continue
        namespace = element.target_namespace or ""
        attributes = {
            name.rsplit("}", 1)[-1]: _attribute_definition(attribute)
            for name, attribute in sorted(element.type.attributes.items())
            if name is not None
        }
        content = getattr(element.type, "content", None)
        declared_children = (
            tuple(content.iter_elements()) if content is not None else ()
        )
        children = sorted(
            {
                _catalog_key(child.name, child.target_namespace or "")
                for child in declared_children
                if child.name is not None
            }
        )
        key = _catalog_key(element.name, namespace)
        elements[key] = {
            "attributes": attributes,
            "allows_custom_children": any(
                child.name is None for child in declared_children
            ),
            "children": children,
            "namespace": namespace,
            "parents": [],
        }

    for parent, definition in elements.items():
        for child in definition["children"]:
            if child in elements:
                elements[child]["parents"].append(parent)
    for definition in elements.values():
        definition["parents"].sort()
    result: dict[str, Any] = {"elements": dict(sorted(elements.items()))}
    if version is not None:
        result["schema_version"] = version
    return result


def _compile_schema(path: Path) -> Any:
    xmlschema = _xmlschema_module()
    try:
        return xmlschema.XMLSchema11(path, allow="local")
    except Exception as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error


def build_hyperview_catalog() -> dict[str, Any]:
    """Build deterministic completion metadata from the bundled XSD set.

    Returns:
        JSON-compatible schema catalog sorted by declaration name.

    Raises:
        TemplateValidationError: If the optional schema dependency is unavailable
            or the bundled schema cannot be compiled.
    """
    return _schema_catalog(
        _compile_schema(get_hyperview_schema_path()),
        version=HYPERVIEW_SCHEMA_VERSION,
    )


def _fingerprint(path: Path) -> tuple[str, int, int]:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, TypeError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return str(resolved), metadata.st_size, metadata.st_mtime_ns


def _guard_local_references(path: Path) -> tuple[Path, ...]:
    root = path.parent.resolve()
    pending = [path.resolve()]
    visited: set[Path] = set()
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            document = etree.parse(current, parser)
        except (OSError, etree.XMLSyntaxError) as error:
            raise TemplateValidationError("schema_invalid", "invalid schema") from error
        for reference in document.getroot().iter():
            if reference.tag not in _REFERENCE_TAGS:
                continue
            location = reference.get("schemaLocation")
            if not location:
                continue
            parsed = urlparse(location)
            if parsed.scheme or parsed.netloc:
                raise TemplateValidationError(
                    "forbidden_schema_reference",
                    "remote schema references are forbidden",
                )
            target = (current.parent / location).resolve()
            if not target.is_relative_to(root):
                raise TemplateValidationError(
                    "forbidden_schema_reference",
                    "schema references must remain inside their local root",
                )
            pending.append(target)
    return tuple(sorted(visited))


@lru_cache(maxsize=64)
def _custom_catalog(fingerprint: tuple[str, int, int]) -> dict[str, Any]:
    path = Path(fingerprint[0])
    _guard_local_references(path)
    return _schema_catalog(_compile_schema(path))


def _target_namespace(path: Path) -> str:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.parse(path, parser).getroot()
    except (OSError, etree.XMLSyntaxError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return root.get("targetNamespace", "")


def _registry_state() -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for configured in get_settings().extra_schemas:
        root = configured.resolve()
        for path in _guard_local_references(root):
            name, size, modified = _fingerprint(path)
            marker = "root:" if path == root else "dependency:"
            state.append((f"{marker}{name}", size, modified))
    return tuple(sorted(state))


@lru_cache(maxsize=64)
def _compile_registry(state: tuple[tuple[str, int, int], ...]) -> Any:
    xmlschema = _xmlschema_module()
    roots = [
        Path(name.removeprefix("root:"))
        for name, _, _ in state
        if name.startswith("root:")
    ]
    locations = [(_target_namespace(path), str(path)) for path in roots]
    try:
        return xmlschema.XMLSchema11(
            get_hyperview_schema_path(),
            allow="local",
            locations=locations,
        )
    except Exception as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error


@lru_cache(maxsize=1)
def _official_catalog() -> dict[str, Any]:
    path = get_hyperview_schema_path().with_name("catalog.json")
    return json.loads(path.read_text(encoding="utf-8"))


def get_hyperview_catalog() -> dict[str, Any]:
    """Return official completion metadata merged with configured local schemas.

    Returns:
        A detached JSON-compatible catalog safe for caller mutation.

    Raises:
        TemplateValidationError: If an extra schema is unsafe, invalid, or
            declares an incompatible duplicate element.
    """
    catalog = deepcopy(_official_catalog())
    elements = catalog["elements"]
    for configured in get_settings().extra_schemas:
        extra = _custom_catalog(_fingerprint(configured))
        for name, definition in extra["elements"].items():
            existing = elements.get(name)
            if existing is not None and existing != definition:
                raise TemplateValidationError(
                    "duplicate_schema_declaration",
                    "schema contains an incompatible duplicate declaration",
                )
            elements[name] = definition
    catalog["elements"] = dict(sorted(elements.items()))
    return catalog


def validate_hyperview_schema(document: str) -> None:
    """Validate rendered HXML against Hyperview 0.110.0 and local extensions.

    Args:
        document: Rendered HXML document or fragment.

    Raises:
        TemplateValidationError: If the document does not match the compiled
            XSD 1.1 registry or the optional dependency is unavailable.
    """
    from .validation import _parse

    root = _parse(document, get_settings().validation)
    validator = _compile_registry(_registry_state())
    try:
        error = next(validator.iter_errors(root), None)
    except Exception as failure:
        raise TemplateValidationError("schema_invalid", "invalid schema") from failure
    if error is None:
        return
    line = getattr(error, "sourceline", None)
    if line is None:
        line = getattr(getattr(error, "elem", None), "sourceline", None)
    raise TemplateValidationError(
        "schema",
        "document does not match schema",
        line=line,
    ) from None


@receiver(
    setting_changed,
    dispatch_uid="dj_hyperview.clear_schema_registry_cache",
    weak=False,
)
def _clear_schema_registry_cache(*, setting: str, **kwargs: Any) -> None:
    del kwargs
    if setting == "HYPERVIEW":
        _official_catalog.cache_clear()
        _custom_catalog.cache_clear()
        _compile_registry.cache_clear()
