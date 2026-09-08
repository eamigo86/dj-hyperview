"""Context-free validation helpers for unsaved Admin HXML drafts."""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.template import TemplateSyntaxError

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import InvalidTemplateName, TemplateValidationError
from dj_hyperview.sources import canonicalize_template_name
from dj_hyperview.validation import validate_template_source


def _diagnostic(
    code: str,
    message: str,
    *,
    template: str | None = None,
    line: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    """Create one safe source-coordinate diagnostic."""
    return {
        "severity": "error",
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
    return {"ok": True, "diagnostics": []}
