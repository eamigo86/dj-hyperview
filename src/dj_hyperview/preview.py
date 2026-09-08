"""Render unsaved Admin drafts without publication or shared template caching.

Providers, sources, and Django template libraries remain trusted application code;
preview is not a CPU, memory, database, or external-side-effect sandbox.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from django.http import HttpRequest
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.utils.module_loading import import_string
from lxml import etree

from .conf import ValidationSettings, get_settings
from .engine import HyperviewEngine
from .exceptions import InvalidTemplateName, TemplateValidationError
from .loaders import template_snapshot
from .resolver import TemplateResolver
from .sources import ResolvedTemplate, TemplateSource
from .sources.base import canonicalize_template_name
from .validation import validate_hxml, validate_template_source

_MESSAGES = {
    "preview_disabled": "Template preview is disabled.",
    "invalid_scenario": "Select a configured example scenario.",
    "invalid_name": "Enter a canonical relative template name.",
    "invalid_content": "Template content must be a string.",
    "context_invalid": "The example provider must return a mapping.",
    "context_error": "The example context could not be prepared.",
    "template_syntax": "Django template syntax is invalid.",
    "template_runtime": "The template could not be rendered with this example.",
    "template_not_found": "A required template could not be resolved.",
    "template_recursion": "Template rendering exceeded the recursion limit.",
    "source_unavailable": "A configured template source could not be loaded.",
    "wrapper_missing_draft": (
        "The configured root did not include the unsaved template."
    ),
    "empty_context": (
        "No example data is configured; missing variables may render empty."
    ),
    "forbidden_declaration": "DTD and entity declarations are forbidden.",
    "invalid_encoding": "XML declarations must use UTF-8.",
    "malformed_xml": "The rendered XML is malformed or cannot be encoded as UTF-8.",
    "max_bytes": "The document exceeds VALIDATION.MAX_BYTES.",
    "max_depth": "The document exceeds VALIDATION.MAX_DEPTH.",
    "max_nodes": "The document exceeds VALIDATION.MAX_NODES.",
    "schema": "The rendered document does not match the configured schema.",
    "schema_invalid": "The configured schema could not be applied safely.",
    "schema_dependency_missing": (
        "Schema validation requires the optional schema dependency."
    ),
    "forbidden_schema_reference": (
        "The configured schema contains a forbidden reference."
    ),
    "duplicate_schema_declaration": (
        "The configured schema has conflicting declarations."
    ),
}


def _positive(value: Any) -> int | None:
    """Keep only valid one-based integer coordinates.

    Args:
        value: Untrusted diagnostic coordinate.

    Returns:
        A positive integer or None for an unknown location.
    """
    return value if type(value) is int and value > 0 else None


def _diagnostic(
    code: str,
    *,
    template: str | None = None,
    space: str | None = None,
    line: int | None = None,
    column: int | None = None,
    warning: bool = False,
) -> dict[str, Any]:
    """Build one diagnostic from package-owned messages and safe metadata.

    Args:
        code: Known package diagnostic code.
        template: Canonical logical template name, when known.
        space: Source or rendered coordinate space, when applicable.
        line: Optional one-based line.
        column: Optional one-based column.
        warning: Whether this diagnostic is nonblocking.

    Returns:
        A JSON-compatible diagnostic without raw exception text or context.
    """
    return {
        "severity": "warning" if warning else "error",
        "code": code,
        "message": _MESSAGES[code],
        "template": template,
        "coordinate_space": space,
        "line": _positive(line),
        "column": _positive(column),
    }


def _failure(diagnostic: dict[str, Any], *, hxml: str | None = None) -> dict[str, Any]:
    """Build a failed result with optional bounded read-only output.

    Args:
        diagnostic: The first blocking diagnostic.
        hxml: Safe bounded output retained only for escaped inspection.

    Returns:
        A failed JSON-compatible preview result.
    """
    return {"ok": False, "hxml": hxml, "diagnostics": [diagnostic]}


def _bounded_output(rendered: str, max_bytes: int) -> str | None:
    """Retain failed output only within the UTF-8 payload budget.

    Args:
        rendered: Unvalidated rendered output.
        max_bytes: Maximum encoded byte length.

    Returns:
        Original output or None when it cannot safely enter the response.
    """
    if len(rendered) > max_bytes:
        return None
    try:
        encoded = rendered.encode("utf-8")
    except UnicodeError:
        return None
    return str(rendered) if len(encoded) <= max_bytes else None


def _validation_diagnostic(
    error: TemplateValidationError, name: str, space: str
) -> dict[str, Any]:
    """Map a validation failure to sanitized source or output coordinates.

    Args:
        error: Validation exception, potentially raised by consumer code.
        name: Canonical logical template name.
        space: Coordinate space for this validation stage.

    Returns:
        A diagnostic with known codes and available parser coordinates.
    """
    code = (
        error.code
        if isinstance(error.code, str) and error.code in _MESSAGES
        else "schema"
    )
    line, column = error.line, error.column
    cause = error.__cause__
    if isinstance(cause, etree.XMLSyntaxError):
        line, column = cause.position
    return _diagnostic(code, template=name, space=space, line=line, column=column)


class _SourceValidationFailure(Exception):
    """Carry safe dependency metadata through Django's compilation boundary."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        """Initialize a source failure without retaining raw content in its text.

        Args:
            diagnostic: Sanitized source diagnostic.
        """
        self.diagnostic = diagnostic
        super().__init__("Preview source failed validation")


class _DraftSource:
    """Resolve only the current unsaved name, ahead of configured sources."""

    def __init__(self, name: str, content: str) -> None:
        """Initialize one request-local draft.

        Args:
            name: Canonical draft name.
            content: Original unsaved source.
        """
        self.name = name
        self.content = content
        self.used = False

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Return the draft when its canonical name is requested.

        Args:
            name: Requested canonical template name.

        Returns:
            Draft source or None when another source must handle the name.
        """
        if name != self.name:
            return None
        self.used = True
        return ResolvedTemplate(name, self.content, name, "preview", "unsaved")


class _PreviewResolver(TemplateResolver):
    """Validate dependencies and replace physical origins with logical metadata."""

    def __init__(
        self, sources: list[TemplateSource], validation: ValidationSettings
    ) -> None:
        """Initialize an uncached request-local resolver.

        Args:
            sources: Draft-first ordered template sources.
            validation: Mandatory source and rendered validation policy.
        """
        super().__init__(sources, cache=None)
        self.validation = validation
        self.logical_origins: dict[str, str] = {}

    def resolve(self, name: str) -> ResolvedTemplate:
        """Resolve and validate source while keeping physical paths private.

        Args:
            name: Requested canonical logical template name.

        Returns:
            Validated source with a request-local diagnostic origin.

        Raises:
            _SourceValidationFailure: If the resolved dependency is unsafe.
            TemplateNotFound: If no configured source resolves the name.
        """
        resolved = super().resolve(name)
        try:
            validate_template_source(resolved.content, config=self.validation)
        except TemplateValidationError as error:
            raise _SourceValidationFailure(
                _validation_diagnostic(error, name, "source")
            ) from error
        origin = f"preview:{name}"
        self.logical_origins[origin] = name
        return replace(resolved, name=name, origin=origin)


def _render_diagnostic(error: Exception, resolver: _PreviewResolver) -> dict[str, Any]:
    """Reduce Django and consumer exceptions to safe logical source metadata.

    Args:
        error: Compilation or render exception.
        resolver: Resolver owning the request-local logical origin map.

    Returns:
        One diagnostic without exception text, tracebacks, or source excerpts.
    """
    if isinstance(error, _SourceValidationFailure):
        return error.diagnostic
    if isinstance(error, TemplateSyntaxError):
        code = "template_syntax"
    elif isinstance(error, TemplateDoesNotExist):
        code = "template_not_found"
    elif isinstance(error, InvalidTemplateName):
        code = "invalid_name"
    elif isinstance(error, RecursionError):
        code = "template_recursion"
    else:
        code = "template_runtime"
    debug = getattr(error, "template_debug", {})
    if not isinstance(debug, dict):
        debug = {}
    origin = debug.get("name")
    name = resolver.logical_origins.get(origin) if isinstance(origin, str) else None
    line = debug.get("line")
    if line is None:
        line = getattr(getattr(error, "token", None), "lineno", None)
    return _diagnostic(code, template=name, space="source", line=line)


def render_preview(
    name: str,
    content: str,
    scenario: str,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    """Render a configured example against an isolated unsaved template.

    Args:
        name: Canonical logical name of the unsaved template.
        content: Original editor content, without formatting or persistence.
        scenario: ID of a server-configured example scenario.
        request: Request passed only to a trusted context provider, never
            implicitly to the template or Django context processors.

    Returns:
        A JSON-compatible result containing ok, hxml, and redacted diagnostics.
        Error locations distinguish source templates from rendered XML. The
        first blocking failure is returned; missing variables are not inferred.
        Failed rendered output is retained only when bounded and UTF-8 encodable,
        for escaped read-only diagnostics, never for visual rendering.

    Raises:
        HyperviewConfigurationError: If package configuration is invalid.
    """
    config = get_settings()
    preview = config.admin.preview
    if not preview.enabled:
        return _failure(_diagnostic("preview_disabled"))
    if not isinstance(scenario, str) or scenario not in preview.scenarios:
        return _failure(_diagnostic("invalid_scenario"))
    selected = preview.scenarios[scenario]
    try:
        canonicalize_template_name(name)
    except InvalidTemplateName:
        return _failure(_diagnostic("invalid_name", space="source"))
    if not isinstance(content, str):
        return _failure(_diagnostic("invalid_content", template=name, space="source"))
    validation = replace(
        config.validation,
        mode="publish_and_render",
        schema=config.validation.schema or "dj_hyperview.validate_hyperview_schema",
    )
    try:
        validate_template_source(content, config=validation)
    except TemplateValidationError as error:
        return _failure(_validation_diagnostic(error, name, "source"))
    draft = _DraftSource(name, content)
    try:
        sources = [
            import_string(source.backend)(**source.options) for source in config.sources
        ]
        resolver = _PreviewResolver([draft, *sources], validation)
        engine = HyperviewEngine(resolver, validation=validation)
        engine.backend.engine.debug = True
    except Exception:
        return _failure(_diagnostic("source_unavailable"))
    root = selected.root_template or name
    with template_snapshot(resolver):
        try:
            template = engine.backend.get_template(root)
        except Exception as error:
            return _failure(_render_diagnostic(error, resolver))
        try:
            context = selected.context
            if callable(context):
                context = context(request, name)
            else:
                context = deepcopy(dict(context))
            if not isinstance(context, Mapping):
                return _failure(_diagnostic("context_invalid"))
            context = dict(context)
        except Exception:
            return _failure(_diagnostic("context_error"))
        try:
            rendered = template.render(context)
        except Exception as error:
            return _failure(_render_diagnostic(error, resolver))
    if not draft.used:
        return _failure(_diagnostic("wrapper_missing_draft"))
    try:
        validate_hxml(rendered, config=validation)
    except TemplateValidationError as error:
        return _failure(
            _validation_diagnostic(error, root, "rendered"),
            hxml=_bounded_output(rendered, validation.max_bytes),
        )
    diagnostics = (
        [_diagnostic("empty_context", warning=True)]
        if scenario == "empty"
        and isinstance(selected.context, Mapping)
        and not selected.context
        else []
    )
    return {"ok": True, "hxml": str(rendered), "diagnostics": diagnostics}
