"""Context-free validation helpers for unsaved Admin HXML drafts."""

from __future__ import annotations

import json
import re
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.template import TemplateSyntaxError
from lxml import etree

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import InvalidTemplateName, TemplateValidationError
from dj_hyperview.schema import (
    HYPERVIEW_NAMESPACE,
    _get_compiled_registry,
    _get_declaration_type,
    _get_static_validation_catalog,
)
from dj_hyperview.sources import canonicalize_template_name
from dj_hyperview.validation import validate_template_source

_STATIC_ROOT = "djhv-static-validation-root"
_DYNAMIC_VALUE = "DJHVSTATICDYNAMIC"
_DJANGO_TOKEN = re.compile(r"({{[\s\S]*?}}|{%[\s\S]*?%}|{#[\s\S]*?#})")
_XML_DECLARATION = re.compile(r"<\?xml(?:\s|\?)[\s\S]*?\?>", re.IGNORECASE)
_RAW_BLOCK = re.compile(r"{%\s*(comment|verbatim)(?:\s+([^\s%]+))?\s*%}")


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


def _mask_template_syntax(content: str) -> tuple[str, str, bool]:
    """Make Django tokens and XML declarations safe inside a synthetic root."""

    marker = _DYNAMIC_VALUE
    while marker in content:
        marker += "_"
    masked_raw = _mask_raw_blocks(content)
    dynamic = _DJANGO_TOKEN.search(masked_raw) is not None

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        return _blank_preserving_lines(token, marker)

    masked = _DJANGO_TOKEN.sub(replace_token, masked_raw)
    return (
        _XML_DECLARATION.sub(
            lambda match: _blank_preserving_lines(match.group(0)), masked
        ),
        marker,
        dynamic,
    )


def _incomplete_schema_diagnostic(name: str, line: int | None) -> dict[str, Any]:
    """Report dynamic source that cannot be checked completely without rendering."""
    return _diagnostic(
        "schema_static_incomplete",
        "Static schema validation could not analyze the complete dynamic "
        "template structure.",
        severity="warning",
        template=name,
        line=line,
    )


def _static_schema_diagnostics(name: str, content: str) -> list[dict[str, Any]]:
    """Check statically visible elements and attributes against the XSD catalog."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    masked, dynamic_marker, has_dynamic_syntax = _mask_template_syntax(content)
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
    incomplete_line = None
    if has_dynamic_syntax or re.search(r"{%\s*verbatim(?:\s|%)", content):
        token = _DJANGO_TOKEN.search(content)
        incomplete_line = content.count("\n", 0, token.start()) + 1 if token else 1
    for element in root.iterdescendants():
        if not isinstance(element.tag, str):
            continue
        qualified_name = etree.QName(element)
        namespace = qualified_name.namespace or ""
        local_name = qualified_name.localname
        if local_name == dynamic_marker:
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
        dynamic_action = key == "behavior" and dynamic_marker in element.get(
            "action", ""
        )
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
            if attribute_name == dynamic_marker:
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
            if dynamic_marker not in value:
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
    try:
        validate_template_source(content, config=config)
        HyperviewEngine(validation=config).backend.from_string(content)
    except TemplateSyntaxError as error:
        token = getattr(error, "token", None)
        return {
            "ok": False,
            "diagnostics": [
                _diagnostic(
                    "django_syntax",
                    "Invalid Django template syntax.",
                    template=name,
                    line=getattr(token, "lineno", None),
                )
            ],
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
        diagnostics = _static_schema_diagnostics(name, content)
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
