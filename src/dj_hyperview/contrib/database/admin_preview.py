"""Bounded JSON transport helpers for the read-only Admin preview."""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse

from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.sources import canonicalize_template_name


def _error(code: str, message: str, status: int) -> JsonResponse:
    """Create a safe diagnostic response from package-owned messages.

    Args:
        code: Stable diagnostic identifier.
        message: Trusted user-facing explanation.
        status: HTTP response status code.

    Returns:
        Structured failure without source or context disclosure.
    """
    return JsonResponse(
        {
            "ok": False,
            "hxml": None,
            "diagnostics": [
                {
                    "severity": "error",
                    "code": code,
                    "message": message,
                    "template": None,
                    "coordinate_space": None,
                    "line": None,
                    "column": None,
                }
            ],
        },
        status=status,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate keys while decoding a JSON object.

    Args:
        pairs: JSON object members in their original order.

    Returns:
        Unambiguous decoded object.

    Raises:
        ValueError: If a key occurs more than once.
    """
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _payload(request: HttpRequest) -> dict[str, str] | JsonResponse:
    """Validate bounded preview transport before running trusted providers.

    Args:
        request: Authorized request with an unconsumed JSON body.

    Returns:
        Exact name, content and scenario strings, or a safe error response.

    Raises:
        HyperviewConfigurationError: If the package settings are invalid.
    """
    config = get_settings()
    if request.content_type != "application/json":
        return _error("invalid_payload", "Expected a JSON request body.", 400)
    # JSON may represent each source byte as a six-byte Unicode escape.
    limit = 6 * config.validation.max_bytes + 16_384
    if settings.DATA_UPLOAD_MAX_MEMORY_SIZE is not None:
        limit = min(limit, settings.DATA_UPLOAD_MAX_MEMORY_SIZE)
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > limit:
            raise RequestDataTooBig
        body = request.read(limit + 1)
        if len(body) > limit:
            raise RequestDataTooBig
        values = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except RequestDataTooBig:
        return _error("input_too_large", "Preview request is too large.", 413)
    except (ValueError, UnicodeError, RecursionError):
        return _error("invalid_payload", "Invalid JSON request body.", 400)
    if (
        not isinstance(values, dict)
        or set(values) != {"name", "content", "scenario"}
        or any(type(value) is not str for value in values.values())
    ):
        return _error(
            "invalid_payload", "Expected name, content and scenario strings only.", 400
        )
    try:
        canonicalize_template_name(values["name"])
        if len(values["name"]) > 255:
            raise InvalidTemplateName(values["name"])
        source_size = len(values["content"].encode("utf-8"))
    except (InvalidTemplateName, UnicodeError):
        return _error(
            "invalid_payload", "Invalid template name or source encoding.", 400
        )
    if source_size > config.validation.max_bytes:
        return _error("input_too_large", "Template source is too large.", 413)
    if values["scenario"] not in config.admin.preview.scenarios:
        return _error("invalid_scenario", "Select a configured preview scenario.", 400)
    return values
