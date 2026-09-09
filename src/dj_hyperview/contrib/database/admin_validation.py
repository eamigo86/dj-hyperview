"""Context-free validation helpers for unsaved Admin HXML drafts."""

from __future__ import annotations

import json
import re
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.template import Origin, TemplateSyntaxError
from django.template.base import DebugLexer, Parser
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
from dj_hyperview.validation import validate_template_source

_STATIC_ROOT = "djhv-static-validation-root"
_DYNAMIC_VALUE = "DJHVSTATICDYNAMIC"
_DYNAMIC_STRUCTURE = "DJHVSTATICSTRUCTURE"
_DJANGO_TOKEN = re.compile(r"({{[\s\S]*?}}|{%[\s\S]*?%}|{#[\s\S]*?#})")
_XML_DECLARATION = re.compile(r"<\?xml(?:\s|\?)[\s\S]*?\?>", re.IGNORECASE)
_XML_OPAQUE_BLOCK = re.compile(
    r"<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>",
    re.IGNORECASE,
)
_RAW_BLOCK = re.compile(r"{%\s*(comment|verbatim)(?:\s+([^\s%]+))?\s*%}")
_LOAD_TAG = re.compile(r"{%\s*load(?:\s|%)")
_MAX_LITERAL_INCLUDES = 64


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
    return marker + "".join("\n" if character == "\n" else " " for character in value)


def _mask_raw_blocks(content: str) -> str:
    """Hide comment and verbatim bodies that Django excludes from rendering."""
    cursor = 0
    masked = content
    while match := _RAW_BLOCK.search(masked, cursor):
        command, name = match.groups()
        suffix = rf"\s+{re.escape(name)}" if name else ""
        closing = re.compile(rf"{{%\s*end{command}{suffix}\s*%}}")
        end = closing.search(masked, match.end())
        if end is None:
            return masked
        span = masked[match.start() : end.end()]
        replacement = _blank_preserving_lines(span)
        masked = masked[: match.start()] + replacement + masked[end.end() :]
        cursor = match.start() + len(replacement)
    return masked


def _xml_token_context(content: str, start: int) -> str:
    """Locate a Django token in XML text, a tag, or a quoted attribute value."""
    lexical = _mask_raw_blocks(content)
    lexical = _DJANGO_TOKEN.sub(
        lambda match: _blank_preserving_lines(match.group(0)), lexical
    )
    lexical = _XML_OPAQUE_BLOCK.sub(
        lambda match: _blank_preserving_lines(match.group(0)), lexical
    )
    in_tag = False
    quote = None
    for character in lexical[:start]:
        if not in_tag:
            if character == "<":
                in_tag = True
            continue

        if quote is not None:
            if character == quote:
                quote = None
            continue

        if character in {'"', "'"}:
            quote = character
        elif character == ">":
            in_tag = False

    if not in_tag:
        return "text"
    return "attribute" if quote is not None else "tag"


def _requires_runtime_analysis(content: str, match: re.Match[str]) -> bool:
    """Return whether one Django token can change statically visible markup."""
    token = match.group(0)
    if token.startswith("{#") or _LOAD_TAG.match(token):
        return False
    context = _xml_token_context(content, match.start())
    if context == "attribute":
        return False
    if token.startswith("{{"):
        return context == "tag"
    command = token[2:-2].strip().split(maxsplit=1)[0]
    scalar_tags = {
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
    return command not in scalar_tags


def _first_runtime_token_line(content: str) -> int | None:
    """Find the first Django token that can alter visible XML structure."""
    masked_raw = _mask_raw_blocks(content)
    for match in _DJANGO_TOKEN.finditer(masked_raw):
        if _requires_runtime_analysis(masked_raw, match):
            return content.count("\n", 0, match.start()) + 1
    return None


def _mask_template_syntax(content: str) -> tuple[str, str, str, bool]:
    """Make Django tokens and XML declarations safe inside a synthetic root."""

    value_marker = _DYNAMIC_VALUE
    while value_marker in content:
        value_marker += "_"
    structure_marker = _DYNAMIC_STRUCTURE
    while structure_marker in content or structure_marker == value_marker:
        structure_marker += "_"
    masked_raw = _mask_raw_blocks(content)
    dynamic_structure = any(
        _requires_runtime_analysis(masked_raw, match)
        for match in _DJANGO_TOKEN.finditer(masked_raw)
    )

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("{#") or _LOAD_TAG.match(token):
            prefix = ""
        elif _requires_runtime_analysis(masked_raw, match):
            prefix = structure_marker
        else:
            prefix = value_marker
        return _blank_preserving_lines(token, prefix)

    masked = _DJANGO_TOKEN.sub(replace_token, masked_raw)
    return (
        _XML_DECLARATION.sub(
            lambda match: _blank_preserving_lines(match.group(0)), masked
        ),
        value_marker,
        structure_marker,
        dynamic_structure,
    )


def _incomplete_schema_diagnostic(name: str, line: int | None) -> dict[str, Any]:
    """Report dynamic source that cannot be checked completely without rendering."""
    return _diagnostic(
        "schema_static_incomplete",
        "Static checks passed. Django markup beginning here can add or remove "
        "XML structure; the final HXML will be validated when served.",
        severity="warning",
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
    django_engine = engine.backend.engine
    origin = Origin(f"hyperview:{name}", template_name=name)
    parser = Parser(
        DebugLexer(content).tokenize(),
        django_engine.template_libraries,
        django_engine.template_builtins,
        origin,
    )
    return parser.parse()


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
    replacements = []
    diagnostics = []
    for node in nodelist.get_nodes_by_type(IncludeNode):
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
        budget[0] += 1
        if budget[0] > _MAX_LITERAL_INCLUDES:
            diagnostics.append(
                _include_error(
                    "django_include_limit",
                    "Literal include expansion exceeds the validation limit.",
                    template=name,
                    line=line,
                )
            )
            continue
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
        child_hxml_context = (
            hxml_context and _xml_token_context(content, start) == "text"
        )
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
    name: str, root: Any, compiled: Any
) -> list[dict[str, Any]]:
    """Validate statically visible child order and placement with the real XSD."""
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
        except Exception as failure:
            raise TemplateValidationError(
                "schema_invalid", "invalid schema"
            ) from failure
    return diagnostics


def _static_schema_diagnostics(name: str, content: str) -> list[dict[str, Any]]:
    """Check statically visible elements and attributes against the XSD catalog."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    masked, value_marker, structure_marker, has_dynamic_syntax = _mask_template_syntax(
        content
    )
    wrapped = f'<{_STATIC_ROOT} xmlns="{HYPERVIEW_NAMESPACE}">{masked}</{_STATIC_ROOT}>'
    try:
        root = etree.fromstring(wrapped.encode(), parser)
    except etree.XMLSyntaxError as error:
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

    catalog = _get_static_validation_catalog()
    elements = catalog["elements"]
    compiled = _get_compiled_registry()
    known_namespaces = {item["namespace"] for item in elements.values()}
    diagnostics = []
    incomplete_line = _first_runtime_token_line(content) if has_dynamic_syntax else None
    if verbatim := re.search(r"{%\s*verbatim(?:\s|%)", content):
        verbatim_line = content.count("\n", 0, verbatim.start()) + 1
        incomplete_line = (
            min(incomplete_line, verbatim_line) if incomplete_line else verbatim_line
        )
    for element in root.iterdescendants():
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
    if not has_dynamic_syntax:
        diagnostics.extend(_static_structure_diagnostics(name, root, compiled))
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
                    if item["code"] in {"schema_structure", "schema_static_incomplete"}
                ),
            ]
        diagnostics = _unique_diagnostics(diagnostics)
        diagnostics.sort(key=lambda item: item["severity"] != "error")
        if any(
            item["severity"] == "error" and item["code"].startswith("django_include_")
            for item in diagnostics
        ):
            diagnostics = [item for item in diagnostics if item["severity"] == "error"]
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
