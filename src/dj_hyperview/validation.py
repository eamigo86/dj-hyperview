"""Safe validation for rendered Hyperview XML."""

import codecs
import re
from pathlib import Path
from threading import RLock
from typing import Any

from django.dispatch import receiver
from django.test.signals import setting_changed
from django.utils.module_loading import import_string
from django.utils.safestring import SafeData, mark_safe
from lxml import etree

from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError

FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
SCHEMA_MESSAGE = "document does not match schema"
XSD_NAMESPACE = "{http://www.w3.org/2001/XMLSchema}"
RESTRICTED_FRAGMENT_ROOTS = frozenset({"body", "doc", "navigator", "screen"})
IGNORED_BLOCKS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"))
XML_ENCODING = re.compile(
    r"^\s*<\?xml\b[^>]*\bencoding\s*=\s*(['\"])([^'\"]+)\1",
    re.IGNORECASE,
)
_SCHEMA_CACHE: dict[Path, tuple[tuple[int, int], etree.XMLSchema]] = {}
_SCHEMA_LOCK = RLock()


def _fail(code: str, message: str) -> None:
    raise TemplateValidationError(code, message)


@receiver(
    setting_changed,
    dispatch_uid="dj_hyperview.clear_compiled_schema_cache",
    weak=False,
)
def _clear_compiled_schema_cache(*, setting: str, **kwargs: Any) -> None:
    del kwargs
    if setting == "HYPERVIEW":
        with _SCHEMA_LOCK:
            _SCHEMA_CACHE.clear()


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        encoding="utf-8",
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


def _inline_django_comment_end(document: str, index: int) -> int | None:
    if not document.startswith("{#", index):
        return None
    end = document.find("#}", index + 2)
    if end < 0:
        return None
    newline = document.find("\n", index + 2)
    if 0 <= newline < end:
        return None
    return end + 2


def _contains_forbidden_declaration(document: str) -> bool:
    if "<!DOCTYPE" not in document and "<!ENTITY" not in document:
        return False
    index = 0
    while index < len(document):
        for opening, closing in IGNORED_BLOCKS:
            if document.startswith(opening, index):
                end = document.find(closing, index + len(opening))
                index = len(document) if end < 0 else end + len(closing)
                break
        else:
            comment_end = _inline_django_comment_end(document, index)
            if comment_end is None:
                comment_end = _django_comment_end(document, index)
            if comment_end is not None:
                index = comment_end
            elif document.startswith(("<!DOCTYPE", "<!ENTITY"), index):
                return True
            else:
                index += 1
    return False


def _encode_utf8(document: str) -> bytes:
    declaration = XML_ENCODING.match(document)
    if declaration is not None:
        try:
            encoding = codecs.lookup(declaration.group(2)).name
        except LookupError:
            encoding = None
        if encoding != "utf-8":
            raise TemplateValidationError(
                "invalid_encoding", "XML declaration must use UTF-8"
            ) from None
    try:
        return document.encode()
    except UnicodeEncodeError as error:
        raise TemplateValidationError("malformed_xml", "invalid XML") from error


def _guard_declarations(document: str) -> bytes:
    encoded = _encode_utf8(document)
    if _contains_forbidden_declaration(document):
        _fail("forbidden_declaration", FORBIDDEN_MESSAGE)
    return encoded


def _guard_document(document: str, config: ValidationSettings) -> bytes:
    encoded = _encode_utf8(document)
    if len(encoded) > config.max_bytes:
        _fail("max_bytes", "document exceeds MAX_BYTES")
    if _contains_forbidden_declaration(document):
        _fail("forbidden_declaration", FORBIDDEN_MESSAGE)
    return encoded


def validate_template_source(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Reject unsafe declarations and oversized source before compilation.

    Args:
        document: Raw template source.
        config: Validation policy and limits.

    Returns:
        The unchanged validated source.
    """
    _guard_document(document, config or get_settings().validation)
    return document


def _parse(document: str, config: ValidationSettings):
    encoded = _guard_document(document, config)
    try:
        root = etree.fromstring(encoded, parser=_parser())
    except etree.XMLSyntaxError as error:
        if "Excessive depth" in str(error):
            raise TemplateValidationError(
                "max_depth", "document exceeds MAX_DEPTH"
            ) from error
        raise TemplateValidationError("malformed_xml", "invalid XML") from error
    except UnicodeError as error:
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


def _compile_schema(schema: str | Path) -> etree.XMLSchema:
    try:
        path = Path(schema).resolve()
        metadata = path.stat()
    except (OSError, TypeError) as error:
        raise TemplateValidationError("schema_invalid", "invalid schema") from error
    fingerprint = (metadata.st_mtime_ns, metadata.st_size)

    with _SCHEMA_LOCK:
        cached = _SCHEMA_CACHE.get(path)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        try:
            schema_document = path.read_text(encoding="utf-8")
            schema_root = etree.fromstring(
                _guard_declarations(schema_document), parser=_parser()
            )
        except TemplateValidationError:
            raise
        except (OSError, UnicodeError, etree.XMLSyntaxError) as error:
            raise TemplateValidationError("schema_invalid", "invalid schema") from error

        references = {
            f"{XSD_NAMESPACE}include",
            f"{XSD_NAMESPACE}import",
            f"{XSD_NAMESPACE}redefine",
        }
        if any(element.tag in references for element in schema_root.iter()):
            _fail(
                "forbidden_schema_reference", "external schema references are forbidden"
            )
        try:
            compiled = etree.XMLSchema(schema_root)
        except etree.XMLSchemaParseError as error:
            raise TemplateValidationError("schema_invalid", "invalid schema") from error
        _SCHEMA_CACHE[path] = (fingerprint, compiled)
        return compiled


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
        _compile_schema(schema).assertValid(root)
    except etree.DocumentInvalid as error:
        raise TemplateValidationError("schema", SCHEMA_MESSAGE) from error


def validate_hxml(document: str, *, config: ValidationSettings | None = None) -> str:
    """Validate a rendered HXML document and return it unchanged.

    Args:
        document: Rendered HXML document.
        config: Validation policy and limits.

    Returns:
        The unchanged validated document.
    """
    resolved = config or get_settings().validation
    root = _parse(document, resolved)
    if resolved.schema is not None:
        _validate_schema(root, document, resolved)
    return document


def validate_fragment_hxml(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Validate the XML shape required by a Hyperview fragment response.

    Args:
        document: Rendered HXML fragment.
        config: Validation policy and limits.

    Returns:
        The unchanged validated fragment.

    Raises:
        TemplateValidationError: If XML is unsafe, malformed, exceeds a limit,
            or uses a client-owned document root.
    """
    root = _parse(document, config or get_settings().validation)
    if etree.QName(root).localname.casefold() in RESTRICTED_FRAGMENT_ROOTS:
        _fail(
            "restricted_fragment_root",
            "fragment root must not be doc, navigator, screen, or body",
        )
    return document


def validate_rendered_hxml(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Apply the configured post-render validation policy.

    Args:
        document: Rendered HXML document.
        config: Validation policy and limits.

    Returns:
        The rendered document after applying the configured policy.
    """
    resolved = config or get_settings().validation
    stripped = document.lstrip()
    document = mark_safe(stripped) if isinstance(document, SafeData) else stripped
    if resolved.mode in {"render", "publish_and_render"}:
        return validate_hxml(document, config=resolved)
    return document
