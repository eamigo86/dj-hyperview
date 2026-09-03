"""Safe validation for rendered Hyperview XML."""

from pathlib import Path

from django.utils.module_loading import import_string
from lxml import etree

from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError

FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
SCHEMA_MESSAGE = "document does not match schema"
XSD_NAMESPACE = "{http://www.w3.org/2001/XMLSchema}"
IGNORED_BLOCKS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"), ("{#", "#}"))


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


def _django_comment_end(document: str, index: int) -> int | None:
    if not document.startswith("{%", index):
        return None
    tag_end = document.find("%}", index + 2)
    if tag_end < 0:
        return None
    bits = document[index + 2 : tag_end].strip().split()
    if not bits or bits[0] != "comment":
        return None
    cursor = tag_end + 2
    while cursor < len(document):
        tag_start = document.find("{%", cursor)
        if tag_start < 0:
            return len(document)
        tag_end = document.find("%}", tag_start + 2)
        if tag_end < 0:
            return len(document)
        bits = document[tag_start + 2 : tag_end].strip().split()
        if bits and bits[0] == "endcomment":
            return tag_end + 2
        cursor = tag_end + 2
    return len(document)


def _contains_forbidden_declaration(document: str) -> bool:
    index = 0
    while index < len(document):
        for opening, closing in IGNORED_BLOCKS:
            if document.startswith(opening, index):
                end = document.find(closing, index + len(opening))
                index = len(document) if end < 0 else end + len(closing)
                break
        else:
            comment_end = _django_comment_end(document, index)
            if comment_end is not None:
                index = comment_end
            elif document.startswith(("<!DOCTYPE", "<!ENTITY"), index):
                return True
            else:
                index += 1
    return False


def _guard_document(document: str, config: ValidationSettings) -> bytes:
    try:
        encoded = document.encode()
    except UnicodeEncodeError as error:
        raise TemplateValidationError("malformed_xml", "invalid XML") from error
    if len(encoded) > config.max_bytes:
        _fail("max_bytes", "document exceeds MAX_BYTES")
    if _contains_forbidden_declaration(document):
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


def validate_rendered_hxml(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Apply the configured post-render validation policy."""
    resolved = config or get_settings().validation
    if resolved.mode in {"render", "publish_and_render"}:
        return validate_hxml(document, config=resolved)
    return document
