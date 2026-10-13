"""Context-free validation helpers for unsaved Admin HXML drafts."""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.template import Origin, TemplateSyntaxError
from django.template.base import TokenType
from django.template.loader_tags import IncludeNode
from lxml import etree

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import (
    InvalidTemplateName,
    SourceUnavailable,
    TemplateNotFound,
    TemplateValidationError,
)
from dj_hyperview.loaders import template_snapshot
from dj_hyperview.schema import (
    HYPERVIEW_NAMESPACE,
    _get_compiled_registry,
    _get_declaration_type,
    _get_static_validation_catalog,
    _schema_validation_message,
)
from dj_hyperview.sources import canonicalize_template_name
from dj_hyperview.validation import (
    _MAX_DEPTH_MESSAGE,
    _check_limits,
    _check_namespace_scope,
    _is_depth_limit_error,
    validate_template_source,
)

from ._template_syntax import _compile_nodelist, _LinearLexer

_STATIC_ROOT = "djhv-static-validation-root"
_DYNAMIC_VALUE = "DJHVSTATICDYNAMIC"
_DYNAMIC_STRUCTURE = "DJHVSTATICSTRUCTURE"
_XML_DECLARATION = re.compile(r"<\?xml(?:\s|\?)", re.IGNORECASE)
_NOT_NEWLINE = re.compile(r"[^\n]")
_SAFE_FILTER = re.compile(r"\|\s*safe\b")
# Tags whose output is one scalar value, so they cannot change the XML structure.
_SCALAR_TAGS = frozenset(
    {
        "blocktranslate",
        "endblocktranslate",
        "firstof",
        "now",
        "static",
        "trans",
        "translate",
        "url",
        "widthratio",
    }
)
_MAX_LITERAL_INCLUDES = 64
_MAX_DIAGNOSTICS = 50
# What only the composed source can add; its elements and attributes are already
# reported once, beside the template that owns them.
_COMPOSED_CODES = frozenset(
    {
        "schema_structure",
        "schema_static_incomplete",
        "max_nodes",
        "max_depth",
        "max_attributes",
    }
)

# What the scanner knows about the XML it is inside while it reads literal text.
_TEXT, _TAG, _DOUBLE_QUOTED, _SINGLE_QUOTED, _COMMENT, _CDATA, _PI = range(7)
_CONTEXT = {
    _TEXT: "text",
    _TAG: "tag",
    _DOUBLE_QUOTED: "attribute",
    _SINGLE_QUOTED: "attribute",
    # Comments, CDATA and processing instructions hide their Django tokens from XML.
    _COMMENT: "text",
    _CDATA: "text",
    _PI: "text",
}
_CLOSER = {
    _DOUBLE_QUOTED: '"',
    _SINGLE_QUOTED: "'",
    _COMMENT: "-->",
    _CDATA: "]]>",
    _PI: "?>",
}
_TAG_DELIMITER = re.compile(r"[\"'>]")
_AFTER_DELIMITER = {">": _TEXT, '"': _DOUBLE_QUOTED, "'": _SINGLE_QUOTED}


def _diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "error",
    template: str | None = None,
    line: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    """Create one safe source-coordinate diagnostic."""
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "template": template,
        "coordinate_space": "source" if template is not None else None,
        "line": line,
        "column": column,
    }


def _error(code: str, message: str, status: int) -> JsonResponse:
    """Return one package-owned transport failure."""
    return JsonResponse(
        {"ok": False, "diagnostics": [_diagnostic(code, message)]}, status=status
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object keys instead of accepting ambiguity."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _blank_preserving_lines(value: str, marker: str = "") -> str:
    """Replace source text while preserving its line-coordinate space."""
    return marker + _NOT_NEWLINE.sub(" ", value)


def _unused_marker(content: str, base: str) -> str:
    """Extend a marker with underscores until the source cannot contain it."""
    longest = max(
        (len(match.group(1)) for match in re.finditer(f"{base}(_*)", content)),
        default=-1,
    )
    return base + "_" * (longest + 1)


def _advance_xml_state(content: str, state: int, position: int, end: int) -> int:
    """Move the XML scanner across the literal text from position to end.

    Each search is bounded by end, so it either finds something and moves on or
    consumes the rest of this text, which keeps the whole scan linear.
    """
    while position < end:
        if state == _TEXT:
            position = content.find("<", position, end)
            if position < 0:
                break
            if content.startswith("<!--", position, end):
                state, position = _COMMENT, position + 4
            elif content.startswith("<![CDATA[", position, end):
                state, position = _CDATA, position + 9
            elif content.startswith("<?", position, end):
                state, position = _PI, position + 2
            else:
                state, position = _TAG, position + 1
        elif state == _TAG:
            match = _TAG_DELIMITER.search(content, position, end)
            if match is None:
                break
            position = match.end()
            state = _AFTER_DELIMITER[match.group()]
        else:
            found = content.find(_CLOSER[state], position, end)
            if found < 0:
                break
            position = found + len(_CLOSER[state])
            state = _TAG if state in {_DOUBLE_QUOTED, _SINGLE_QUOTED} else _TEXT
    return state


def _blank_xml_declarations(text: str) -> str:
    """Blank every XML declaration, which a synthetic root cannot contain."""
    parts = []
    cursor = 0
    for opener in _XML_DECLARATION.finditer(text):
        if opener.start() < cursor:
            continue
        closer = text.find("?>", opener.end())
        if closer < 0:
            break
        parts.append(text[cursor : opener.start()])
        parts.append(_blank_preserving_lines(text[opener.start() : closer + 2]))
        cursor = closer + 2
    parts.append(text[cursor:])
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class _TemplateScan:
    """Django syntax of one source, classified by the XML around each token."""

    masked: str
    value_marker: str
    structure_marker: str
    # The XML context of every token that renders output, by its start offset.
    contexts: dict[int, str]
    first_structure_line: int | None
    verbatim_line: int | None


def _scan_template(content: str) -> _TemplateScan:
    """Locate Django syntax in XML once, in one pass over Django's own tokens.

    Django decides what is a tag, a comment or a verbatim body, so the tokens come
    from its lexer, and a tag split over lines is plain text, as it is when served.
    The XML state is advanced only across the literal text between tokens.
    """
    value_marker = _unused_marker(content, _DYNAMIC_VALUE)
    structure_marker = _unused_marker(content, _DYNAMIC_STRUCTURE)
    tokens = _LinearLexer(content).tokenize()
    parts: list[str] = []
    contexts: dict[int, str] = {}
    first_structure_line = verbatim_line = None
    end_comments: list[int] | None = None
    state = _TEXT
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        start, end = token.position
        if token.token_type is TokenType.TEXT:
            state = _advance_xml_state(content, state, start, end)
            parts.append(content[start:end])
            continue
        command = ""
        if token.token_type is TokenType.BLOCK:
            command = token.contents.split(maxsplit=1)[0] if token.contents else ""
            if command == "comment":
                if end_comments is None:
                    end_comments = [
                        position
                        for position, other in enumerate(tokens)
                        if other.token_type is TokenType.BLOCK
                        and other.contents == "endcomment"
                    ]
                at = bisect_left(end_comments, index)
                if at < len(end_comments):
                    closing = tokens[end_comments[at]]
                    parts.append(
                        _blank_preserving_lines(content[start : closing.position[1]])
                    )
                    index = end_comments[at] + 1
                    continue
            elif token.contents[:9] in {"verbatim", "verbatim "}:
                verbatim_line = verbatim_line or token.lineno
                # Django's lexer turns the body into text, so the next other token
                # is the closing tag.
                closing = index
                while closing < len(tokens) and (
                    tokens[closing].token_type is TokenType.TEXT
                ):
                    closing += 1
                stop = (
                    tokens[closing].position[1]
                    if closing < len(tokens)
                    else len(content)
                )
                parts.append(_blank_preserving_lines(content[start:stop]))
                index = closing + 1
                continue
        if token.token_type is TokenType.COMMENT or command == "load":
            parts.append(_blank_preserving_lines(content[start:end]))
            continue
        context = _CONTEXT[state]
        contexts[start] = context
        if token.token_type is TokenType.VAR:
            # Markup can come from a variable in a tag, or one marked safe in text.
            structure = context == "tag" or (
                context == "text" and _SAFE_FILTER.search(token.contents) is not None
            )
        else:
            structure = context != "attribute" and command not in _SCALAR_TAGS
        if structure and first_structure_line is None:
            first_structure_line = token.lineno
        parts.append(
            _blank_preserving_lines(
                content[start:end], structure_marker if structure else value_marker
            )
        )
    return _TemplateScan(
        _blank_xml_declarations("".join(parts)),
        value_marker,
        structure_marker,
        contexts,
        first_structure_line,
        verbatim_line,
    )


def _first_runtime_token_line(content: str) -> int | None:
    """Find the first Django token that can alter visible XML structure."""
    return _scan_template(content).first_structure_line


def _mask_template_syntax(content: str) -> tuple[str, str, str, bool]:
    """Make Django tokens and XML declarations safe inside a synthetic root."""
    scan = _scan_template(content)
    return (
        scan.masked,
        scan.value_marker,
        scan.structure_marker,
        scan.first_structure_line is not None,
    )


def _incomplete_schema_diagnostic(name: str, line: int | None) -> dict[str, Any]:
    """Report dynamic source that cannot be checked completely without rendering."""
    return _diagnostic(
        "schema_static_incomplete",
        "Static checks passed. Final HXML will be validated when served.",
        severity="info",
        template=name,
        line=line,
    )


def _django_syntax_message(error: TemplateSyntaxError) -> str:
    """Expose bounded parser guidance without template origins or source excerpts."""
    raw = getattr(error, "raw_error_message", None)
    if not isinstance(raw, str):
        raw = str(error)
    message = " ".join(raw.split())
    if not message or len(message) > 500:
        return "Invalid Django template syntax."
    return f"Django template syntax error: {message}"


def _django_syntax_diagnostic(name: str, error: TemplateSyntaxError) -> dict[str, Any]:
    """Return one source-scoped diagnostic from Django's own parser."""
    token = getattr(error, "token", None)
    return _diagnostic(
        "django_syntax",
        _django_syntax_message(error),
        template=name,
        line=getattr(token, "lineno", None),
    )


def _compile_django_source(engine: HyperviewEngine, name: str, content: str) -> Any:
    """Compile source with exact token spans and a canonical template origin."""
    origin = Origin(f"hyperview:{name}", template_name=name)
    return _compile_nodelist(engine.backend.engine, content, origin)


def _literal_include_name(node: IncludeNode) -> str | None:
    """Return a context-independent include target, or None for dynamic input."""
    expression = node.template
    value = expression.var
    if expression.filters or not isinstance(value, str):
        return None
    return value


def _flatten_include(content: str, replaced_token: str) -> str:
    """Inline structural content without shifting the parent's source lines."""
    flattened = re.sub(r"\r\n|\r|\n", " ", content)
    line_endings = "".join(re.findall(r"\r\n|\r|\n", replaced_token))
    return flattened + line_endings


def _include_error(
    code: str, message: str, *, template: str, line: int | None
) -> dict[str, Any]:
    """Create one actionable literal-include diagnostic."""
    return _diagnostic(code, message, template=template, line=line)


def _expand_literal_includes(
    engine: HyperviewEngine,
    name: str,
    content: str,
    *,
    stack: tuple[str, ...],
    budget: list[int],
    hxml_context: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve and recursively validate context-independent Django includes."""
    nodelist = _compile_django_source(engine, name, content)
    includes = nodelist.get_nodes_by_type(IncludeNode)
    contexts = _scan_template(content).contexts if includes else {}
    replacements = []
    diagnostics = []
    for node in includes:
        start, end = node.token.position
        target = _literal_include_name(node)
        if target is None:
            continue
        line = node.token.lineno
        if target in stack:
            diagnostics.append(
                _include_error(
                    "django_include_cycle",
                    f'Literal include cycle detected through "{target}".',
                    template=name,
                    line=line,
                )
            )
            continue
        if budget[0] >= _MAX_LITERAL_INCLUDES:
            # Expansion stops here and in every caller, with one note, because the
            # includes that were not expanded leave the result incomplete, not wrong.
            if budget[0] == _MAX_LITERAL_INCLUDES:
                budget[0] += 1
                diagnostics.append(_incomplete_schema_diagnostic(name, line))
            break
        budget[0] += 1
        try:
            template = engine.get_template(target)
        except InvalidTemplateName:
            diagnostics.append(
                _include_error(
                    "django_include_invalid",
                    "Included template name is invalid.",
                    template=name,
                    line=line,
                )
            )
            continue
        except TemplateNotFound:
            diagnostics.append(
                _include_error(
                    "django_include_missing",
                    f'Included template "{target}" was not found.',
                    template=name,
                    line=line,
                )
            )
            continue
        except SourceUnavailable:
            diagnostics.append(
                _include_error(
                    "django_include_unavailable",
                    "Included template source is unavailable.",
                    template=name,
                    line=line,
                )
            )
            continue
        except TemplateSyntaxError as error:
            diagnostics.append(_django_syntax_diagnostic(target, error))
            continue
        resolved = template.origin.resolved
        child_hxml_context = hxml_context and contexts.get(start) == "text"
        expanded, child_diagnostics = _expand_literal_includes(
            engine,
            resolved.name,
            resolved.content,
            stack=(*stack, target),
            budget=budget,
            hxml_context=child_hxml_context,
        )
        diagnostics.extend(child_diagnostics)
        if child_hxml_context:
            diagnostics.extend(_static_schema_diagnostics(resolved.name, expanded))
            replacements.append(
                (start, end, _flatten_include(expanded, content[start:end]))
            )

    for start, end, replacement in sorted(replacements, reverse=True):
        content = content[:start] + replacement + content[end:]
    return content, diagnostics


def _unique_diagnostics(
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve diagnostic order while removing repeated recursive findings."""
    result = []
    seen = set()
    for diagnostic in diagnostics:
        identity = tuple(diagnostic.items())
        if identity in seen:
            continue
        seen.add(identity)
        result.append(diagnostic)
    return result


def _static_structure_diagnostics(
    name: str, root: Any, compiled: Any, limit: int
) -> list[dict[str, Any]]:
    """Validate statically visible child order and placement with the real XSD.

    The check stops once it has found limit diagnostics.
    """
    diagnostics = []
    seen = set()
    for candidate in root:
        if not isinstance(candidate.tag, str):
            continue
        try:
            errors = compiled.iter_errors(candidate)
            for error in errors:
                invalid_child = getattr(error, "invalid_child", None)
                reason = getattr(error, "reason", "") or ""
                structural = invalid_child is not None or (
                    isinstance(reason, str)
                    and (
                        "is not complete" in reason
                        or "character data between child elements not allowed" in reason
                    )
                )
                if not structural:
                    continue
                if invalid_child is not None:
                    line = getattr(invalid_child, "sourceline", None)
                else:
                    line = getattr(error, "sourceline", None)
                if line is None:
                    line = getattr(getattr(error, "elem", None), "sourceline", None)
                message = _schema_validation_message(error)
                if reason == "character data between child elements not allowed":
                    element = etree.QName(error.elem).localname
                    message = f'text content is not allowed inside element "{element}"'
                message = message[:1].upper() + message[1:]
                if not message.endswith("."):
                    message += "."
                identity = (line, message)
                if identity in seen:
                    continue
                seen.add(identity)
                diagnostics.append(
                    _diagnostic(
                        "schema_structure",
                        message,
                        template=name,
                        line=line,
                    )
                )
                if len(diagnostics) >= limit:
                    return diagnostics
        except Exception as failure:
            raise TemplateValidationError(
                "schema_invalid", "invalid schema"
            ) from failure
    return diagnostics


def _limit_diagnostic(name: str, code: str, message: str) -> dict[str, Any]:
    """Report a resource limit with the code and wording rendering uses."""
    return _diagnostic(code, f"{message[:1].upper()}{message[1:]}.", template=name)


def _static_schema_diagnostics(name: str, content: str) -> list[dict[str, Any]]:
    """Check statically visible elements and attributes against the XSD catalog.

    The same limits as rendering apply before any schema work, and the list stops
    one entry past the number the caller will show.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    scan = _scan_template(content)
    value_marker, structure_marker = scan.value_marker, scan.structure_marker
    has_dynamic_syntax = scan.first_structure_line is not None
    wrapped = (
        f'<{_STATIC_ROOT} xmlns="{HYPERVIEW_NAMESPACE}">{scan.masked}</{_STATIC_ROOT}>'
    )
    try:
        root = etree.fromstring(wrapped.encode(), parser)
    except etree.XMLSyntaxError as error:
        if _is_depth_limit_error(error):
            return [_limit_diagnostic(name, "max_depth", _MAX_DEPTH_MESSAGE)]
        line = error.position[0] if error.position else None
        if has_dynamic_syntax:
            return [_incomplete_schema_diagnostic(name, line)]
        return [
            _diagnostic(
                "xml_syntax",
                "Template source is not well-formed XML.",
                template=name,
                line=line,
            )
        ]
    try:
        _check_limits(root, get_settings().validation)
        _check_namespace_scope(root)
    except TemplateValidationError as error:
        return [_limit_diagnostic(name, error.code, error.message)]

    catalog = _get_static_validation_catalog()
    elements = catalog["elements"]
    compiled = _get_compiled_registry()
    known_namespaces = {item["namespace"] for item in elements.values()}
    diagnostics: list[dict[str, Any]] = []
    incomplete_line = scan.first_structure_line
    if scan.verbatim_line is not None:
        incomplete_line = min(incomplete_line or scan.verbatim_line, scan.verbatim_line)
    for element in root.iterdescendants():
        if len(diagnostics) > _MAX_DIAGNOSTICS:
            break
        if not isinstance(element.tag, str):
            continue
        qualified_name = etree.QName(element)
        namespace = qualified_name.namespace or ""
        local_name = qualified_name.localname
        if local_name == structure_marker:
            incomplete_line = incomplete_line or element.sourceline
            continue
        key = (
            local_name
            if namespace == HYPERVIEW_NAMESPACE
            else f"{{{namespace}}}{local_name}"
        )
        definition = elements.get(key)
        if definition is None:
            if not namespace or namespace in known_namespaces:
                diagnostics.append(
                    _diagnostic(
                        "schema_element",
                        f'Element "{local_name}" is not declared by the selected '
                        "schema.",
                        template=name,
                        line=element.sourceline,
                    )
                )
            continue
        dynamic_action = key == "behavior" and value_marker in element.get("action", "")
        if dynamic_action:
            incomplete_line = incomplete_line or element.sourceline
        if key == "behavior" and not dynamic_action:
            definition = catalog["behavior_variants"].get(
                element.get("action"), definition
            )
        selected_type = _get_declaration_type(
            compiled.maps.elements[element.tag], element
        )
        allowed = definition["attributes"]
        present = set()
        dynamic_attributes = False
        for raw_name in element.attrib:
            attribute = etree.QName(raw_name)
            attribute_name = attribute.localname
            if attribute_name == structure_marker:
                dynamic_attributes = True
                incomplete_line = incomplete_line or element.sourceline
                continue
            attribute_key = (
                raw_name if attribute.namespace is not None else attribute_name
            )
            present.add(attribute_key)
            attribute_definition = allowed.get(attribute_key)
            if attribute_definition is None:
                if dynamic_action and attribute.namespace is None:
                    # Without the rendered action, custom fields cannot be selected.
                    continue
                if (
                    attribute.namespace is not None
                    and definition["allows_custom_attributes"]
                ):
                    continue
                diagnostics.append(
                    _diagnostic(
                        "schema_attribute",
                        f'Attribute "{attribute_name}" is not allowed on '
                        f'element "{local_name}".',
                        template=name,
                        line=element.sourceline,
                    )
                )
                continue
            value = element.attrib[raw_name]
            invalid_value = False
            if value_marker not in value and structure_marker not in value:
                declared_attribute = selected_type.attributes[attribute_key]
                invalid_value = not declared_attribute.type.is_valid(value)
            if invalid_value:
                diagnostics.append(
                    _diagnostic(
                        "schema_attribute_value",
                        f'Attribute "{attribute_name}" on element "{local_name}" '
                        "has a value not allowed by the selected schema.",
                        template=name,
                        line=element.sourceline,
                    )
                )
        for attribute_key, attribute_definition in sorted(allowed.items()):
            if (
                not dynamic_attributes
                and attribute_definition["required"]
                and attribute_key not in present
            ):
                attribute_name = etree.QName(attribute_key).localname
                diagnostics.append(
                    _diagnostic(
                        "schema_required_attribute",
                        f'Required attribute "{attribute_name}" is missing from '
                        f'element "{local_name}".',
                        template=name,
                        line=element.sourceline,
                    )
                )
    if not has_dynamic_syntax and len(diagnostics) <= _MAX_DIAGNOSTICS:
        diagnostics.extend(
            _static_structure_diagnostics(
                name, root, compiled, _MAX_DIAGNOSTICS + 1 - len(diagnostics)
            )
        )
    if incomplete_line is not None:
        diagnostics.append(_incomplete_schema_diagnostic(name, incomplete_line))
    return diagnostics


def validation_payload(request: HttpRequest) -> dict[str, str] | JsonResponse:
    """Decode one bounded, exact validation request body.

    Args:
        request: Authorized Admin request with an unconsumed JSON body.

    Returns:
        Exact name and content strings, or a safe transport error response.
    """
    config = get_settings()
    if request.content_type != "application/json":
        return _error("invalid_payload", "Expected a JSON request body.", 400)
    limit = 6 * config.validation.max_bytes + 16_384
    if settings.DATA_UPLOAD_MAX_MEMORY_SIZE is not None:
        limit = min(limit, settings.DATA_UPLOAD_MAX_MEMORY_SIZE)
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > limit:
            return _error("input_too_large", "Validation request is too large.", 413)
        body = request.read(limit + 1)
        if len(body) > limit:
            return _error("input_too_large", "Validation request is too large.", 413)
        values = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except RequestDataTooBig:
        return _error("input_too_large", "Validation request is too large.", 413)
    except (ValueError, UnicodeError, RecursionError):
        return _error("invalid_payload", "Invalid JSON request body.", 400)
    if (
        not isinstance(values, dict)
        or set(values) != {"name", "content"}
        or any(type(value) is not str for value in values.values())
    ):
        return _error("invalid_payload", "Expected name and content strings only.", 400)
    try:
        source_size = len(values["content"].encode("utf-8"))
    except UnicodeError:
        return _error("invalid_payload", "Invalid template source encoding.", 400)
    if source_size > config.validation.max_bytes:
        return _error("input_too_large", "Template source is too large.", 413)
    return values


def _too_deep(name: str) -> dict[str, Any]:
    """Report source that Django's recursive parser cannot compile."""
    return {
        "ok": False,
        "diagnostics": [
            _diagnostic("too_deep", "Template is nested too deeply.", template=name)
        ],
    }


def _limit_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first diagnostics, errors before notes, and say if more existed."""
    if len(diagnostics) <= _MAX_DIAGNOSTICS:
        return diagnostics
    return [
        *diagnostics[:_MAX_DIAGNOSTICS],
        _diagnostic(
            "diagnostics_truncated", "More diagnostics were omitted.", severity="info"
        ),
    ]


def validate_draft_source(name: str, content: str) -> dict[str, Any]:
    """Validate an unsaved template without context, rendering, or persistence.

    Args:
        name: Candidate canonical template name from the current form.
        content: Unsaved template source from the current editor buffer.

    Returns:
        A JSON-compatible result containing safe source diagnostics.
    """
    if len(name) > 255:
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic("invalid_name", "Enter a canonical template name.")
            ],
        }
    try:
        canonicalize_template_name(name)
    except InvalidTemplateName:
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic("invalid_name", "Enter a canonical template name.")
            ],
        }

    if not content.strip():
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic(
                    "empty_source",
                    "Enter Hyperview template source.",
                    template=name,
                )
            ],
        }

    config = get_settings().validation
    engine = HyperviewEngine(validation=config)
    try:
        validate_template_source(content, config=config)
        with template_snapshot(engine.resolver):
            expanded, include_diagnostics = _expand_literal_includes(
                engine,
                name,
                content,
                stack=(name,),
                budget=[0],
            )
    except TemplateSyntaxError as error:
        return {
            "ok": False,
            "diagnostics": [_django_syntax_diagnostic(name, error)],
        }
    except RecursionError:
        return _too_deep(name)
    except TemplateValidationError as error:
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic(
                    error.code,
                    "Invalid Hyperview template source.",
                    template=name,
                    line=error.line,
                    column=error.column,
                )
            ],
        }
    try:
        source_diagnostics = _static_schema_diagnostics(name, content)
        if expanded == content:
            diagnostics = [*source_diagnostics, *include_diagnostics]
        else:
            composed_diagnostics = _static_schema_diagnostics(name, expanded)
            diagnostics = [
                *(
                    item
                    for item in source_diagnostics
                    if item["code"] != "schema_static_incomplete"
                ),
                *include_diagnostics,
                *(
                    item
                    for item in composed_diagnostics
                    if item["code"] in _COMPOSED_CODES
                ),
            ]
        diagnostics = _unique_diagnostics(diagnostics)
        diagnostics.sort(key=lambda item: item["severity"] != "error")
        if any(
            item["severity"] == "error" and item["code"].startswith("django_include_")
            for item in diagnostics
        ):
            diagnostics = [item for item in diagnostics if item["severity"] == "error"]
        diagnostics = _limit_diagnostics(diagnostics)
    except RecursionError:
        return _too_deep(name)
    except TemplateValidationError as error:
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic(
                    error.code,
                    "Static schema validation is unavailable.",
                )
            ],
        }
    if diagnostics:
        return {
            "ok": not any(item["severity"] == "error" for item in diagnostics),
            "diagnostics": diagnostics,
        }
    return {"ok": True, "diagnostics": []}
