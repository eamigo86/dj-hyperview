"""Hyperview XSD resources and deterministic editor catalog generation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import unquote, urlparse

from django.dispatch import receiver
from django.test.signals import setting_changed
from lxml import etree

from ._schema_duplicates import _guard_duplicate_declarations
from ._schema_extensions import _EMPTY_EXTENSIONS, _SchemaExtensions
from ._schema_overlay import _generated_overlay
from .conf import get_settings
from .exceptions import TemplateValidationError

HYPERVIEW_SCHEMA_VERSION = "0.110.0"
HYPERVIEW_VALIDATION_CONTRACT = "automatic-xsd-v1"
_SCHEMA_REVISION = "hyperview-0.110.0-corrected-r2"
HYPERVIEW_NAMESPACE = "https://hyperview.org/hyperview"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema"
_SCHEMA_ROOT = Path(__file__).with_name("schemas") / HYPERVIEW_SCHEMA_VERSION
_MAX_SCHEMA_FILES = 256
_REGISTRY_LOCK = RLock()
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
    """Return the unmodified upstream root schema path without Django settings.

    Returns:
        Absolute path to the versioned upstream Hyperview schema entry point.
    """
    return _SCHEMA_ROOT / "hyperview.xsd"


def _xmlschema_module() -> Any:
    try:
        import xmlschema
    except ImportError as error:
        raise TemplateValidationError(
            "schema_dependency",
            "XSD validation requires the installed xmlschema dependency",
        ) from error
    return xmlschema


def _attribute_definition(attribute: Any) -> dict[str, Any]:
    values = sorted(str(value) for value in (attribute.type.enumeration or ()))
    return {"enum": values, "required": attribute.use == "required"}


def _catalog_key(name: str, namespace: str) -> str:
    local = name.rsplit("}", 1)[-1]
    return local if namespace == HYPERVIEW_NAMESPACE else f"{{{namespace}}}{local}"


def _schema_catalog(
    schema: Any, *, version: str | None = None, qualified: bool = False
) -> dict[str, Any]:
    """Build deterministic metadata while preserving legacy output by default.

    Args:
        schema: Compiled schema whose declarations supply completion metadata.
        version: Optional upstream version label.
        qualified: Whether to emit format 2 with qualified attribute identities.

    Returns:
        Detached JSON-compatible element metadata.
    """
    elements: dict[str, Any] = {}
    declarations = sorted(
        schema.maps.elements.values(), key=lambda item: item.name or ""
    )
    for element in declarations:
        if not element.name or element.target_namespace == XSD_NAMESPACE:
            continue
        namespace = element.target_namespace or ""
        element_type = _base_declaration_type(element)
        declared_attributes = (
            {} if element_type.is_simple() else element_type.attributes
        )
        attributes = {
            (name if qualified else name.rsplit("}", 1)[-1]): _attribute_definition(
                attribute
            )
            for name, attribute in sorted(
                declared_attributes.items(), key=lambda pair: pair[0] or ""
            )
            if name is not None
        }
        content = getattr(element_type, "content", None)
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
    if qualified:
        result["catalog_format"] = 2
        result["behavior_variants"] = {}
    return result


def _base_declaration_type(declaration: Any) -> Any:
    """Return the standard fallback, not anyType introduced by alternatives."""
    alternatives = getattr(declaration, "alternatives", ())
    if alternatives and alternatives[-1].elem.get("test") is None:
        return alternatives[-1].type
    return declaration.type


def _get_declaration_type(declaration: Any, element: Any) -> Any:
    """Select the exact XSD type for a statically known element action.

    Args:
        declaration: Compiled element declaration from the shared registry.
        element: Parsed source element with statically known attributes.

    Returns:
        The XSD type selected by the element alternative table.
    """
    return declaration.get_alternative_type(element)


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
        element_type = _base_declaration_type(element)
        declared_attributes = (
            () if element_type.is_simple() else tuple(element_type.attributes.items())
        )
        attributes = {
            name: _attribute_definition(attribute)
            for name, attribute in sorted(
                declared_attributes, key=lambda pair: pair[0] or ""
            )
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
        TemplateValidationError: If the required schema dependency is unavailable
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
def _cached_registry(
    state: tuple[_SchemaDependencies, ...],
    extensions: _SchemaExtensions = _EMPTY_EXTENSIONS,
) -> Any:
    """Compile the corrected registry for one guarded dependency snapshot.

    Args:
        state: Local schema entry points and transitive dependency fingerprints.
        extensions: Immutable typed registrations for the trusted overlay.

    Returns:
        A complete XSD 1.1 registry cached for this exact contract.

    Raises:
        TemplateValidationError: If dependencies or declarations are invalid.
    """
    xmlschema = _xmlschema_module()
    schema_path = _SCHEMA_ROOT / "r2" / "hyperview.xsd"
    roots = [Path(dependencies.root) for dependencies in state]
    locations = [(_target_namespace(path), str(path)) for path in roots]
    try:
        compiled = xmlschema.XMLSchema11(
            _generated_overlay(schema_path, extensions)
            if extensions != _EMPTY_EXTENSIONS
            else schema_path,
            allow="local",
            locations=locations,
            use_fallback=False,
            defuse="always",
        )
    except Exception as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    _require_complete_schema(compiled)
    if state:
        _guard_duplicate_declarations(
            compiled, [_compile_schema(path) for path in roots]
        )
    return compiled


def _compile_registry(
    state: tuple[_SchemaDependencies, ...],
    extensions: _SchemaExtensions = _EMPTY_EXTENSIONS,
) -> Any:
    """Serialize cache lookup and compilation for one immutable registry snapshot.

    Args:
        state: Guarded dependency fingerprints.
        extensions: Normalized typed registrations.

    Returns:
        The cached compiled registry, compiled once under concurrent first use.
    """
    with _REGISTRY_LOCK:
        return _cached_registry(state, extensions)


def get_hyperview_catalog() -> dict[str, Any]:
    """Return detached completion metadata for the automatic corrected registry.

    Returns:
        Format 2 metadata including registered typed behavior variants.

    Raises:
        TemplateValidationError: If a dependency is unsafe, invalid, or conflicts.
    """
    config = get_settings()
    return deepcopy(_active_catalog(_registry_state(), config.schema_extensions))


@lru_cache(maxsize=64)
def _cached_static_validation_catalog(
    state: tuple[_SchemaDependencies, ...],
    extensions: _SchemaExtensions = _EMPTY_EXTENSIONS,
) -> dict[str, Any]:
    """Compile namespace-preserving schema metadata for one registry state."""
    compiled = _compile_registry(state, extensions)
    catalog = _static_validation_catalog(compiled)
    catalog["catalog_format"] = 2
    catalog["behavior_variants"] = _behavior_variants(
        compiled, extensions, catalog["elements"]["behavior"]
    )
    return catalog


def _get_static_validation_catalog() -> dict[str, Any]:
    """Return detached schema metadata for context-free Admin validation."""
    config = get_settings()
    return deepcopy(
        _cached_static_validation_catalog(_registry_state(), config.schema_extensions)
    )


def _get_registry_snapshot() -> tuple[tuple[Any, ...], Any]:
    """Inspect dependencies and return a contract identity and compiled registry.

    Returns:
        Immutable revision/dependency/registration identity and its validator.

    Raises:
        TemplateValidationError: If any dependency is unsafe or inconsistent.
    """
    config = get_settings()
    state = _registry_state()
    return (
        (_SCHEMA_REVISION, state, config.schema_extensions),
        _compile_registry(state, config.schema_extensions),
    )


def _get_compiled_registry() -> Any:
    """Return the shared validator after checking all configured dependencies."""
    return _get_registry_snapshot()[1]


def _behavior_variants(
    compiled: Any, extensions: _SchemaExtensions, baseline: dict[str, Any]
) -> dict[str, Any]:
    """Describe each selected behavior type without mixing unrelated actions.

    Args:
        compiled: Registry containing the generated behavior alternative table.
        extensions: Immutable registrations in deterministic action order.
        baseline: Common metadata shape for completion or static validation.

    Returns:
        Complete per-action element definitions with detached attributes.
    """
    variants = {}
    declaration = compiled.maps.elements[f"{{{HYPERVIEW_NAMESPACE}}}behavior"]
    for index, (action, _attributes) in enumerate(extensions.behaviors):
        variant = deepcopy(baseline)
        variant["attributes"] = {
            name: _attribute_definition(attribute)
            for name, attribute in sorted(
                declaration.alternatives[index].type.attributes.items()
            )
        }
        variants[action] = variant
    return variants


@lru_cache(maxsize=64)
def _active_catalog(
    state: tuple[_SchemaDependencies, ...],
    extensions: _SchemaExtensions,
) -> dict[str, Any]:
    """Build qualified, action-specific metadata from the actual r2 registry.

    Args:
        state: Guarded transitive dependency fingerprints.
        extensions: Normalized immutable registrations.

    Returns:
        Cached metadata shared through detached copies at the public boundary.
    """
    compiled = _compile_registry(state, extensions)
    catalog = _schema_catalog(
        compiled, version=HYPERVIEW_SCHEMA_VERSION, qualified=True
    )
    baseline = catalog["elements"]["behavior"]
    catalog["behavior_variants"] = _behavior_variants(compiled, extensions, baseline)
    baseline["attributes"]["action"]["enum"] = sorted(
        set(baseline["attributes"]["action"]["enum"])
        | {name for name, _ in extensions.behaviors}
    )
    return catalog


def _validate_schema_root(root: Any, validator: Any) -> None:
    """Validate one already guarded XML tree without parsing a second time.

    Args:
        root: Safely parsed rendered document or fragment.
        validator: Compiled corrected registry from the current snapshot.

    Raises:
        TemplateValidationError: If validation fails or the registry is invalid.
    """
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


def validate_hyperview_schema(document: str) -> None:
    """Validate rendered HXML with the mandatory corrected registry.

    Args:
        document: Rendered HXML document or fragment.

    Raises:
        TemplateValidationError: If the document is unsafe or violates the XSD.
    """
    from .validation import _validate_hxml_result

    _validate_hxml_result(document)


@receiver(
    setting_changed,
    dispatch_uid="dj_hyperview.clear_schema_registry_cache",
    weak=False,
)
def _clear_schema_registry_cache(*, setting: str, **kwargs: Any) -> None:
    del kwargs
    if setting == "HYPERVIEW":
        with _REGISTRY_LOCK:
            _cached_registry.cache_clear()
        _cached_static_validation_catalog.cache_clear()
        _active_catalog.cache_clear()
