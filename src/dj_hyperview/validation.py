"""Safe validation for rendered Hyperview XML."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from encodings import normalize_encoding
from encodings.aliases import aliases as _CODEC_ALIASES
from typing import Any

from django.template.base import TokenType
from django.utils.safestring import SafeData, mark_safe
from lxml import etree

from ._template_lexer import _forward_find, _LinearLexer, _unclosed_cost
from .conf import ValidationSettings, get_settings
from .exceptions import TemplateValidationError
from .validators import _XML_ILLEGAL_CHARACTERS

_BOM = "\ufeff"
_XML_WHITESPACE = " \t\r\n"
# Fixed caps on one element and on the path from the root to it. The widest element
# the bundled schema declares has 126 attributes, and real documents bind the default
# namespace and one or two prefixes. xmlschema reads an element's namespace map
# several times, and once more for each in-scope prefix when the element declares a
# namespace, while lxml rebuilds the map from every ancestor. The cost of an accepted
# document therefore grows with the product of the two namespace caps. Measured with
# 20,000 leaves that each rebind a prefix: about 28 s at 64 prefixes and 256
# declarations, about 1.1 s at 8 and 32, against 0.6 s for plain leaves.
_MAX_ATTRIBUTES = 128
_MAX_NAMESPACES = 8
_MAX_NAMESPACE_DECLARATIONS = 32
# Django compiles a template with a lexer that reads on to the end of the line for every
# "{{", "{%" or "{#" that closes no tag, so a line of them takes quadratic time: 40,000
# on one line (120 KB) took 10.5 s. The sum of what it reads for them, counted in
# linear time by _unclosed_cost, is limited to 10 million characters, about 45 ms of
# its time. A source at the limit that is otherwise 1,000,000 bytes of ordinary tags
# compiles in 0.39 s, against 0.35 s without the unclosed openers, and a source of
# real tags costs nothing.
_MAX_UNCLOSED_COST = 10_000_000
_DEPTH_LIMIT_MESSAGE = "Excessive depth in document"
_MAX_DEPTH_MESSAGE = "document exceeds MAX_DEPTH"
FORBIDDEN_MESSAGE = "DTD and entity declarations are forbidden"
ILLEGAL_CHARACTERS_MESSAGE = "document contains characters not allowed in XML 1.0"
RESTRICTED_FRAGMENT_ROOTS = frozenset({"body", "doc", "navigator", "screen"})
IGNORED_BLOCKS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"))
XML_ENCODING = re.compile(
    r"^\ufeff?\s*<\?xml\b[^>]*\bencoding\s*=\s*(['\"])([^'\"]+)\1",
    re.IGNORECASE,
)
# The EncName production of XML 1.0, capped at a length no codec name reaches.
# The name comes from the document, so it is never handed to codecs.lookup, which
# keeps every name it is given, found or not, for the life of the process.
_ENCODING_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
# Every construct the declaration scan acts on starts at "<!", which opens a comment,
# a CDATA section or a declaration, or at "<?", a processing instruction.
_XML_CANDIDATE = re.compile(r"<[!?]")


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


def _template_literal_text(source: str) -> str:
    """Join the literal text of template source that Django renders as written.

    Django's lexer decides what a comment, a verbatim body or a tag is, and its
    comment tag ends at the first tag named exactly endcomment, so the tokens come
    from the same linear lexer that the Admin check uses. Everything else that
    renders output counts as empty, because only a declaration spelled out in the
    source can be told apart from the data that fills it in.
    """
    tokens = _LinearLexer(source).tokenize()
    parts = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.token_type is TokenType.TEXT:
            parts.append(token.contents)
        elif token.token_type is TokenType.BLOCK and token.contents.split(maxsplit=1)[
            :1
        ] == ["comment"]:
            # An unclosed comment is Django's compile error, so the rest is skipped.
            while index < len(tokens) and not (
                tokens[index].token_type is TokenType.BLOCK
                and tokens[index].contents == "endcomment"
            ):
                index += 1
            index += 1
    return "".join(parts)


def _contains_forbidden_declaration(
    document: str, *, template_source: bool = False
) -> bool:
    """Scan forward, reusing delimiter searches rather than rescanning suffixes.

    Between candidate positions nothing can match, so the scan jumps to the next
    candidate instead of testing every character.
    """
    if "<!DOCTYPE" not in document and "<!ENTITY" not in document:
        return False
    if template_source:
        document = _template_literal_text(document)
    find = _forward_find(document)
    index = 0
    while index < len(document):
        for opening, closing in IGNORED_BLOCKS:
            if document.startswith(opening, index):
                end = find(closing, index + len(opening))
                index = len(document) if end < 0 else end + len(closing)
                break
        else:
            if document.startswith(("<!DOCTYPE", "<!ENTITY"), index):
                return True
            following = _XML_CANDIDATE.search(document, index + 1)
            index = len(document) if following is None else following.start()
    return False


def _is_utf8_encoding_name(name: str) -> bool:
    """Decide whether an encoding name selects UTF-8, without a codec lookup.

    The registry resolves a name by normalizing it and reading the alias table
    that this function reads too, so both give the same answer for every name of
    the XML grammar. A name outside that grammar, such as one with a NUL or a
    lone surrogate, is never UTF-8, and no name is remembered.

    Args:
        name: Encoding name taken from a document or a header.

    Returns:
        Whether the name is a spelling of UTF-8.
    """
    if not _ENCODING_NAME.fullmatch(name):
        return False
    key = normalize_encoding(name).lower()
    resolved = _CODEC_ALIASES.get(key) or _CODEC_ALIASES.get(key.replace(".", "_"))
    return (resolved or key) == "utf_8"


def _encode_utf8(document: str) -> bytes:
    declaration = XML_ENCODING.match(document)
    if declaration is not None and not _is_utf8_encoding_name(declaration.group(2)):
        raise TemplateValidationError(
            "invalid_encoding", "XML declaration must use UTF-8"
        )
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
    if template_source:
        if _unclosed_cost(document, _MAX_UNCLOSED_COST) > _MAX_UNCLOSED_COST:
            _fail("max_unclosed", "unclosed Django delimiters exceed the scan limit")
    return encoded


def validate_template_source(
    document: str, *, config: ValidationSettings | None = None
) -> str:
    """Reject unsafe declarations and oversized source before compilation.

    Besides the byte limit, the source may not make Django's lexer waste more than a
    fixed amount of work on unclosed template delimiters, which fails with the code
    max_unclosed.

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

    An element's nsmap is rebuilt from all of its ancestors, and schema validation
    reads it several times for every element, and once for each in-scope prefix of an
    element that declares a namespace. Its cost is therefore the declarations on the
    path times the prefixes in scope, and two caps keep that product small. The
    distinct prefixes in scope are capped, and so is every declaration on the path
    from the root, because rebinding the same few prefixes at each level keeps the
    distinct count low while the path keeps growing. Counting bindings as the walk
    enters and leaves the elements that declare them needs only the declarations
    themselves, and a flood of declarations fails at the first one over a cap.
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


def _is_depth_limit_error(error: etree.XMLSyntaxError) -> bool:
    """Tell libxml2's depth ceiling from any other syntax error.

    libxml2 echoes element and attribute names into its messages, so the word alone
    proves nothing: only a resource-limit error about depth is MAX_DEPTH, or libxml2's
    own message for it, which before 2.13 had another error code.
    """
    message = str(error)
    return message.startswith(_DEPTH_LIMIT_MESSAGE) or (
        error.code == etree.ErrorTypes.ERR_RESOURCE_LIMIT
        and "depth" in message.casefold()
    )


def _check_limits(roots: Iterable[etree._Element], config: ValidationSettings) -> None:
    """Bound the nodes, depth and attributes of the trees below the given roots.

    The walk is iterative, linear and stops at the first node over a limit. It reads
    the attributes of elements only: a processing instruction builds its attrib with
    a regular expression that is quadratic in its data, so it must never be asked.
    The attribute cap applies before the schema walk decodes them.
    """
    nodes = 0
    pending = [(root, 1) for root in roots]
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if depth > config.max_depth:
            _fail("max_depth", _MAX_DEPTH_MESSAGE)
        if nodes > config.max_nodes:
            _fail("max_nodes", "document exceeds MAX_NODES")
        if isinstance(node.tag, str) and len(node.attrib) > _MAX_ATTRIBUTES:
            _fail("max_attributes", f"element exceeds {_MAX_ATTRIBUTES} attributes")
        pending.extend((child, depth + 1) for child in node)


def _parse(document: str, config: ValidationSettings) -> etree._Element:
    encoded = _guard_document(document, config)
    # After the guard, so that size, declaration and encoding failures keep their
    # codes when the document also has an illegal character.
    if _XML_ILLEGAL_CHARACTERS.search(document):
        _fail("malformed_xml", ILLEGAL_CHARACTERS_MESSAGE)
    try:
        root = etree.fromstring(encoded, parser=_parser())
    except etree.XMLSyntaxError as error:
        if _is_depth_limit_error(error):
            raise TemplateValidationError("max_depth", _MAX_DEPTH_MESSAGE) from error
        raise TemplateValidationError("malformed_xml", "invalid XML") from error

    _check_limits([root], config)
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
