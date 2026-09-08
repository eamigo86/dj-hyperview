"""Safe validation for rendered Hyperview XML."""

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.utils.safestring import SafeData, mark_safe
from lxml import etree

from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError

FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
RESTRICTED_FRAGMENT_ROOTS = frozenset({"body", "doc", "navigator", "screen"})
IGNORED_BLOCKS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"))
XML_ENCODING = re.compile(
    r"^\ufeff?\s*<\?xml\b[^>]*\bencoding\s*=\s*(['\"])([^'\"]+)\1",
    re.IGNORECASE,
)
DJANGO_COMMENT = re.compile(r"{%\s*comment(?=\s|%})")
DJANGO_ENDCOMMENT = re.compile(r"{%\s*endcomment(?=\s|%})")


def _fail(code: str, message: str) -> None:
    raise TemplateValidationError(code, message)


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        encoding="utf-8",
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
    )


def _django_comment_end(
    document: str, index: int, find: Callable[[str, int], int]
) -> int | None:
    if DJANGO_COMMENT.match(document, index) is None:
        return None
    tag_end = find("%}", index + 2)
    if tag_end < 0:
        return None
    cursor = tag_end + 2
    while cursor < len(document):
        tag_start = find("{%", cursor)
        if tag_start < 0:
            return len(document)
        tag_end = find("%}", tag_start + 2)
        if tag_end < 0:
            return len(document)
        if DJANGO_ENDCOMMENT.match(document, tag_start) is not None:
            return tag_end + 2
        cursor = tag_end + 2
    return len(document)


def _inline_django_comment_end(
    document: str, index: int, find: Callable[[str, int], int]
) -> int | None:
    if not document.startswith("{#", index):
        return None
    end = find("#}", index + 2)
    if end < 0:
        return None
    newline = find("\n", index + 2)
    if 0 <= newline < end:
        return None
    return end + 2


def _contains_forbidden_declaration(
    document: str, *, template_source: bool = False
) -> bool:
    """Scan forward, reusing delimiter searches rather than rescanning suffixes."""
    if "<!DOCTYPE" not in document and "<!ENTITY" not in document:
        return False
    positions: dict[str, int] = {}

    def find(marker: str, start: int) -> int:
        # Callers advance monotonically for each marker. A previous hit remains
        # valid until consumed; a previous miss covers every later suffix.
        position = positions.get(marker)
        if position is None or 0 <= position < start:
            position = positions[marker] = document.find(marker, start)
        return position

    index = 0
    while index < len(document):
        for opening, closing in IGNORED_BLOCKS:
            if document.startswith(opening, index):
                end = find(closing, index + len(opening))
                index = len(document) if end < 0 else end + len(closing)
                break
        else:
            comment_end = None
            if template_source:
                comment_end = _inline_django_comment_end(document, index, find)
                if comment_end is None:
                    comment_end = _django_comment_end(document, index, find)
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


def _guard_document(
    document: str, config: ValidationSettings, *, template_source: bool = False
) -> bytes:
    encoded = _encode_utf8(document)
    if len(encoded) > config.max_bytes:
        _fail("max_bytes", "document exceeds MAX_BYTES")
    if _contains_forbidden_declaration(document, template_source=template_source):
        _fail("forbidden_declaration", FORBIDDEN_MESSAGE)
    return encoded


def validate_template_source(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Reject unsafe declarations and oversized source before compilation.

    Args:
        document: Raw template source.
        config: Validated byte, depth and node limits.

    Returns:
        The unchanged validated source.
    """
    _guard_document(document, config or get_settings().validation, template_source=True)
    return document


def _parse(document: str, config: ValidationSettings) -> etree._Element:
    encoded = _guard_document(document, config)
    try:
        root = etree.fromstring(encoded, parser=_parser())
    except etree.XMLSyntaxError as error:
        if "depth" in str(error).casefold():
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


@dataclass(frozen=True, slots=True)
class _ValidatedHxml:
    """Private exact rendered output and the immutable contract that accepted it."""

    text: str
    content: bytes
    contract_identity: tuple[Any, ...]


def _validation_contract_identity(
    *, config: ValidationSettings | None = None, fragment: bool = False
) -> tuple[Any, ...]:
    """Resolve the current guarded contract for a private rendered-result handoff.

    Args:
        config: Explicit validated limits, or current configured limits.
        fragment: Whether the bare-fragment root restriction is required.

    Returns:
        Registry revision, dependency and extension snapshot, limits and root mode.
    """
    from .schema import _get_registry_snapshot

    resolved = config or get_settings().validation
    identity, _ = _get_registry_snapshot()
    return (*identity, resolved, fragment)


def _is_current_hxml_result(
    result: _ValidatedHxml,
    *,
    config: ValidationSettings | None = None,
    fragment: bool = False,
) -> bool:
    """Check a private result against dependencies and limits at handoff time.

    Args:
        result: Internal validated output, never inferred from a public string.
        config: Explicit limits, or current configured limits.
        fragment: Whether the receiving path requires a bare fragment.

    Returns:
        Whether its exact contract is still current.
    """
    return result.contract_identity == _validation_contract_identity(
        config=config, fragment=fragment
    )


def _validate_hxml_result(
    document: str, *, config: ValidationSettings | None = None, fragment: bool = False
) -> _ValidatedHxml:
    """Parse once and apply mandatory safety, root and corrected XSD validation.

    Args:
        document: Complete rendered document or fragment.
        config: Explicit validated limits, or current configured limits.
        fragment: Whether to reject client-owned document roots.

    Returns:
        Exact text and UTF-8 bytes bound to the checked registry and limits.

    Raises:
        TemplateValidationError: If safety, shape, limits or XSD validation fails.
    """
    from .schema import _get_registry_snapshot, _validate_schema_root

    resolved = config or get_settings().validation
    root = _parse(document, resolved)
    if fragment and etree.QName(root).localname.casefold() in RESTRICTED_FRAGMENT_ROOTS:
        _fail(
            "restricted_fragment_root",
            "fragment root must not be doc, navigator, screen, or body",
        )
    identity, registry = _get_registry_snapshot()
    _validate_schema_root(root, registry)
    return _ValidatedHxml(
        document, document.encode("utf-8"), (*identity, resolved, fragment)
    )


def validate_hxml(document: str, *, config: ValidationSettings | None = None) -> str:
    """Validate a rendered HXML document and return it unchanged.

    Args:
        document: Rendered HXML document.
        config: Validated byte, depth and node limits.

    Returns:
        The unchanged validated document.
    """
    return _validate_hxml_result(document, config=config).text


def validate_fragment_hxml(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Validate the XML shape required by a Hyperview fragment response.

    Args:
        document: Rendered HXML fragment.
        config: Validated byte, depth and node limits.

    Returns:
        The unchanged validated fragment.

    Raises:
        TemplateValidationError: If XML is unsafe, malformed, exceeds a limit,
            or uses a client-owned document root.
    """
    return _validate_hxml_result(document, config=config, fragment=True).text


def validate_rendered_hxml(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Normalize leading render whitespace and apply mandatory XSD validation.

    Args:
        document: Rendered HXML document.
        config: Validated byte, depth and node limits.

    Returns:
        The normalized rendered document after mandatory safety and XSD validation.
    """
    resolved = config or get_settings().validation
    stripped = document.lstrip()
    document = mark_safe(stripped) if isinstance(document, SafeData) else stripped
    return validate_hxml(document, config=resolved)
