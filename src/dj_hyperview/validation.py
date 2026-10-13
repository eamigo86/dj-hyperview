"""Safe validation for rendered Hyperview XML."""

import codecs
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.utils.safestring import SafeData, mark_safe
from lxml import etree

from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError
from .validators import _XML_ILLEGAL_CHARACTERS

_BOM = "\ufeff"
_XML_WHITESPACE = " \t\r\n"
_MAX_ATTRIBUTES = 256
_MAX_NAMESPACES = 64
_MAX_NAMESPACE_DECLARATIONS = 256
FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
ILLEGAL_CHARACTERS_MESSAGE = "document contains characters not allowed in XML 1.0"
RESTRICTED_FRAGMENT_ROOTS = frozenset({"body", "doc", "navigator", "screen"})
IGNORED_BLOCKS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"))
XML_ENCODING = re.compile(
    r"^\ufeff?\s*<\?xml\b[^>]*\bencoding\s*=\s*(['\"])([^'\"]+)\1",
    re.IGNORECASE,
)
DJANGO_COMMENT = re.compile(r"{%\s*comment(?=\s|%})")
DJANGO_ENDCOMMENT = re.compile(r"{%\s*endcomment(?=\s|%})")
# Every construct the declaration scan acts on starts at one of these: "<!" opens a
# comment, a CDATA section or a declaration, "<?" a processing instruction, and
# "{#" or "{%" a Django comment, which only template source recognizes.
_XML_CANDIDATE = re.compile(r"<[!?]")
_TEMPLATE_CANDIDATE = re.compile(r"<[!?]|\{[#%]")


def _fail(code: str, message: str) -> None:
    raise TemplateValidationError(code, message)


def _rendered_hxml_text(rendered: str) -> str:
    """Drop template preamble whitespace, and nothing else, after an initial BOM.

    Only the four XML whitespace characters are removed. str.lstrip would also
    discard control characters and Unicode spaces that the document must not have.
    """
    bom = _BOM if rendered.startswith(_BOM) else ""
    stripped = bom + rendered[len(bom) :].lstrip(_XML_WHITESPACE)
    return mark_safe(stripped) if isinstance(rendered, SafeData) else stripped


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
    """Scan forward, reusing delimiter searches rather than rescanning suffixes.

    Between candidate positions nothing can match, so the scan jumps to the next
    candidate instead of testing every character.
    """
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

    candidate = _TEMPLATE_CANDIDATE if template_source else _XML_CANDIDATE
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
                following = candidate.search(document, index + 1)
                index = len(document) if following is None else following.start()
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
        # Only a lone surrogate cannot be encoded, and XML 1.0 forbids it.
        raise TemplateValidationError(
            "malformed_xml", ILLEGAL_CHARACTERS_MESSAGE
        ) from error


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


def _check_namespace_scope(root: etree._Element) -> None:
    """Bound the namespaces on any element and its ancestor path in linear time.

    An element's nsmap is rebuilt from all of its ancestors, so schema validation,
    which reads it for every element, costs depth times declarations. Two caps keep
    that product small. The distinct prefixes in scope are capped, and so is every
    declaration on the path from the root, because rebinding the same few prefixes
    at each level keeps the distinct count low while the path keeps growing.
    Counting bindings as the walk enters and leaves the elements that declare them
    needs only the declarations themselves, and a flood of declarations fails at
    the first one over a cap.
    """
    in_scope: Counter[str] = Counter()
    declared: list[str] = []
    for event, binding in etree.iterwalk(root, events=("start-ns", "end-ns")):
        if event == "start-ns":
            prefix = binding[0]
            declared.append(prefix)
            in_scope[prefix] += 1
            if len(declared) > _MAX_NAMESPACE_DECLARATIONS:
                _fail(
                    "max_attributes",
                    f"element exceeds {_MAX_NAMESPACE_DECLARATIONS} namespace "
                    "declarations on its ancestor path",
                )
            if len(in_scope) > _MAX_NAMESPACES:
                _fail(
                    "max_attributes",
                    f"element exceeds {_MAX_NAMESPACES} in-scope namespaces",
                )
        else:
            prefix = declared.pop()
            in_scope[prefix] -= 1
            if not in_scope[prefix]:
                del in_scope[prefix]


def _parse(document: str, config: ValidationSettings) -> etree._Element:
    encoded = _guard_document(document, config)
    # After the guard, so that size, declaration and encoding failures keep their
    # codes when the document also has an illegal character.
    if _XML_ILLEGAL_CHARACTERS.search(document):
        _fail("malformed_xml", ILLEGAL_CHARACTERS_MESSAGE)
    try:
        root = etree.fromstring(encoded, parser=_parser())
    except etree.XMLSyntaxError as error:
        # libxml2 echoes element and attribute names into its messages, so the word
        # alone proves nothing: only a resource-limit error about depth is MAX_DEPTH.
        if (
            error.code == etree.ErrorTypes.ERR_RESOURCE_LIMIT
            and "depth" in str(error).casefold()
        ):
            raise TemplateValidationError(
                "max_depth", "document exceeds MAX_DEPTH"
            ) from error
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
        # XSD work grows super-linearly with per-element attributes. Only elements
        # have any: a processing instruction builds attrib with a regular expression
        # that is quadratic in its data, so it must never be asked.
        if isinstance(node.tag, str) and len(node.attrib) > _MAX_ATTRIBUTES:
            _fail("max_attributes", f"element exceeds {_MAX_ATTRIBUTES} attributes")
        pending.extend((child, depth + 1) for child in node)
    # After the node and depth limits, so they bound this walk as well.
    _check_namespace_scope(root)
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
    return validate_hxml(_rendered_hxml_text(document), config=resolved)
