"""Safe validation for rendered Hyperview XML."""

from pathlib import Path

from django.utils.module_loading import import_string
from lxml import etree

from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError

FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
SCHEMA_MESSAGE = "document does not match schema"
XSD_NAMESPACE = "{http://www.w3.org/2001/XMLSchema}"


def _fail(code: str, message: str) -> None:
    raise TemplateValidationError(code, message)


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
    )


def _guard_document(document: str, config: ValidationSettings) -> bytes:
    encoded = document.encode()
    if len(encoded) > config.max_bytes:
        _fail("max_bytes", "document exceeds MAX_BYTES")
    if "<!DOCTYPE" in document or "<!ENTITY" in document:
        _fail("forbidden_declaration", FORBIDDEN_MESSAGE)
    return encoded


def validate_template_source(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Reject unsafe declarations and oversized source before compilation."""
    _guard_document(document, config or get_settings().validation)
    return document


def _parse(document: str, config: ValidationSettings):
    encoded = _guard_document(document, config)
    try:
        root = etree.fromstring(encoded, parser=_parser())
    except (UnicodeError, etree.XMLSyntaxError) as error:
        raise TemplateValidationError("malformed_xml", "invalid XML") from error

    nodes = 0
    pending = [(root, 1)]
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if depth > config.max_depth:
            _fail("max_depth", "document exceeds MAX_DEPTH")
        if nodes > config.max_nodes:
            _fail("max_nodes", "document exceeds MAX_NODES")
        pending.extend((child, depth + 1) for child in node)
    return root


def _schema_callable(schema):
    if callable(schema):
        return schema
    if isinstance(schema, str) and not Path(schema).is_file():
        try:
            return import_string(schema)
        except ImportError as error:
            raise TemplateValidationError("schema_invalid", "invalid schema") from error
    return None


def _validate_schema(root, document: str, config: ValidationSettings) -> None:
    schema = config.schema
    validator = _schema_callable(schema)
    if validator is not None:
        try:
            accepted = validator(document)
        except Exception as error:
            raise TemplateValidationError("schema", SCHEMA_MESSAGE) from error
        if accepted is False:
            _fail("schema", SCHEMA_MESSAGE)
        return

    try:
        schema_document = Path(schema).read_text(encoding="utf-8")
        schema_root = etree.fromstring(
            _guard_document(schema_document, config), parser=_parser()
        )
    except (OSError, UnicodeError, etree.XMLSyntaxError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error

    references = {
        f"{XSD_NAMESPACE}include",
        f"{XSD_NAMESPACE}import",
        f"{XSD_NAMESPACE}redefine",
    }
    if any(element.tag in references for element in schema_root.iter()):
        _fail("forbidden_schema_reference", "external schema references are forbidden")
    try:
        etree.XMLSchema(schema_root).assertValid(root)
    except etree.XMLSchemaParseError as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    except etree.DocumentInvalid as error:
        raise TemplateValidationError("schema", SCHEMA_MESSAGE) from error


def validate_hxml(document: str, *, config: ValidationSettings | None = None) -> str:
    """Validate a rendered HXML document and return it unchanged."""
    resolved = config or get_settings().validation
    root = _parse(document, resolved)
    if resolved.schema is not None:
        _validate_schema(root, document, resolved)
    return document
