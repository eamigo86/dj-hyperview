"""Hyperview XSD resources and deterministic editor catalog generation."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from django.dispatch import receiver
from django.test.signals import setting_changed
from lxml import etree

from .conf import SchemaProfile, get_settings
from .exceptions import TemplateValidationError

HYPERVIEW_SCHEMA_VERSION = "0.110.0"
HYPERVIEW_NAMESPACE = "https://hyperview.org/hyperview"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema"
_SCHEMA_ROOT = Path(__file__).with_name("schemas") / HYPERVIEW_SCHEMA_VERSION
_MAX_SCHEMA_FILES = 256
_OVERRIDE_TAG = f"{{{XSD_NAMESPACE}}}override"
_REFERENCE_TAGS = frozenset(
    {
        f"{{{XSD_NAMESPACE}}}include",
        f"{{{XSD_NAMESPACE}}}import",
        f"{{{XSD_NAMESPACE}}}redefine",
    }
)


@dataclass(frozen=True, slots=True)
class _SchemaDependencies:
    root: str
    files: tuple[tuple[str, int, int], ...]


def get_hyperview_schema_path() -> Path:
    """Return the bundled root schema path.

    Returns:
        Absolute path to the versioned Hyperview schema entry point.
    """
    return _SCHEMA_ROOT / "hyperview.xsd"


def _profile_resources(profile: SchemaProfile) -> tuple[Path, Path]:
    catalog_path = get_hyperview_schema_path().with_name("catalog.json")
    if profile == "compatible-0.110.0":
        return _SCHEMA_ROOT / "compatibility" / "hyperview.xsd", catalog_path
    return get_hyperview_schema_path(), catalog_path


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


def _static_validation_catalog(schema: Any) -> dict[str, Any]:
    """Build namespace-preserving metadata for Admin source validation."""
    elements = {}
    declarations = sorted(
        schema.maps.elements.values(), key=lambda item: item.name or ""
    )
    for element in declarations:
        if not element.name or element.target_namespace == XSD_NAMESPACE:
            continue
        namespace = element.target_namespace or ""
        declared_attributes = tuple(element.type.attributes.items())
        attributes = {
            name: _attribute_definition(attribute)
            for name, attribute in sorted(declared_attributes)
            if name is not None
        }
        elements[_catalog_key(element.name, namespace)] = {
            "allows_custom_attributes": any(
                name is None for name, _attribute in declared_attributes
            ),
            "attributes": attributes,
            "namespace": namespace,
        }
    return {"elements": dict(sorted(elements.items()))}


def _require_complete_schema(compiled: Any) -> Any:
    """Reject warnings that leave a root or imported schema only partly compiled."""
    if any(schema.warnings for schema in compiled.maps.iter_schemas()):
        raise TemplateValidationError("schema_invalid", "incomplete schema")
    return compiled


def _compile_schema(path: Path) -> Any:
    xmlschema = _xmlschema_module()
    try:
        compiled = xmlschema.XMLSchema11(
            path, allow="local", use_fallback=False, defuse="always"
        )
    except Exception as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return _require_complete_schema(compiled)


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
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return str(resolved), metadata.st_size, metadata.st_mtime_ns


def _guard_local_references(path: Path) -> tuple[Path, ...]:
    try:
        root = path.parent.resolve(strict=True)
        entrypoint = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    pending = [entrypoint]
    scheduled = {entrypoint}
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
        if document.docinfo.doctype:
            raise TemplateValidationError(
                "forbidden_declaration", "DTD and entity declarations are forbidden"
            )
        for reference in document.getroot().iter():
            if reference.tag == _OVERRIDE_TAG:
                raise TemplateValidationError(
                    "forbidden_schema_reference",
                    "extra schemas must not override declarations",
                )
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
            if (
                unquote(location) != location
                or "\\" in location
                or location != location.strip()
            ):
                raise TemplateValidationError(
                    "forbidden_schema_reference",
                    "schema references must use plain local paths",
                )
            try:
                target = (current.parent / location).resolve()
            except (OSError, RuntimeError, ValueError) as error:
                raise TemplateValidationError(
                    "schema_invalid", "invalid schema"
                ) from error
            if not target.is_relative_to(root):
                raise TemplateValidationError(
                    "forbidden_schema_reference",
                    "schema references must remain inside their local root",
                )
            if target not in scheduled:
                if len(scheduled) >= _MAX_SCHEMA_FILES:
                    raise TemplateValidationError(
                        "schema_invalid", "schema exceeds the local file limit"
                    )
                scheduled.add(target)
                pending.append(target)
    return tuple(sorted(visited))


@lru_cache(maxsize=64)
def _custom_catalog(
    profile: SchemaProfile, dependencies: _SchemaDependencies
) -> dict[str, Any]:
    del profile
    return _schema_catalog(_compile_schema(Path(dependencies.root)))


def _target_namespace(path: Path) -> str:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.parse(path, parser).getroot()
    except (OSError, etree.XMLSyntaxError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return root.get("targetNamespace", "")


def _registry_state() -> tuple[_SchemaDependencies, ...]:
    state: list[_SchemaDependencies] = []
    for configured in get_settings().extra_schemas:
        paths = _guard_local_references(configured)
        state.append(
            _SchemaDependencies(
                str(configured.resolve()), tuple(_fingerprint(path) for path in paths)
            )
        )
    return tuple(state)


@lru_cache(maxsize=64)
def _compile_registry(
    profile: SchemaProfile, state: tuple[_SchemaDependencies, ...]
) -> Any:
    xmlschema = _xmlschema_module()
    schema_path, _ = _profile_resources(profile)
    roots = [Path(dependencies.root) for dependencies in state]
    locations = [(_target_namespace(path), str(path)) for path in roots]
    try:
        compiled = xmlschema.XMLSchema11(
            schema_path,
            allow="local",
            locations=locations,
            use_fallback=False,
            defuse="always",
        )
    except Exception as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return _require_complete_schema(compiled)


@lru_cache(maxsize=2)
def _official_catalog(profile: SchemaProfile) -> dict[str, Any]:
    _, path = _profile_resources(profile)
    return json.loads(path.read_text(encoding="utf-8"))


def get_hyperview_catalog() -> dict[str, Any]:
    """Return profile completion metadata merged with configured local schemas.

    Returns:
        A detached JSON-compatible catalog with schema profile metadata, safe
        for caller mutation.

    Raises:
        TemplateValidationError: If an extra schema is unsafe, invalid, or
            declares an incompatible duplicate element.
    """
    profile = get_settings().schema_profile
    state = _registry_state()
    catalog = deepcopy(_official_catalog(profile))
    catalog["schema_profile"] = profile
    elements = catalog["elements"]
    for dependencies in state:
        extra = _custom_catalog(profile, dependencies)
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


@lru_cache(maxsize=64)
def _cached_static_validation_catalog(
    profile: SchemaProfile, state: tuple[_SchemaDependencies, ...]
) -> dict[str, Any]:
    """Compile namespace-preserving schema metadata for one registry state."""
    return _static_validation_catalog(_compile_registry(profile, state))


def _get_static_validation_catalog() -> dict[str, Any]:
    """Return detached schema metadata for context-free Admin validation."""
    profile = get_settings().schema_profile
    state = _registry_state()
    return deepcopy(_cached_static_validation_catalog(profile, state))


def validate_hyperview_schema(document: str) -> None:
    """Validate rendered HXML against the selected profile and local extensions.

    Args:
        document: Rendered HXML document or fragment.

    Raises:
        TemplateValidationError: If the document does not match the compiled
            XSD 1.1 registry or the optional dependency is unavailable.
    """
    from .validation import _parse

    root = _parse(document, get_settings().validation)
    validator = _compile_registry(get_settings().schema_profile, _registry_state())
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
        _cached_static_validation_catalog.cache_clear()
