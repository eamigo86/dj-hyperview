"""Generic HTTP views for the package-owned Django consumer."""

from typing import Any

from django.http import HttpRequest
from django.views.decorators.http import require_GET, require_http_methods

from dj_hyperview import (
    HyperviewEngine,
    HyperviewRequestDetails,
    HyperviewResponse,
    HyperviewTemplateResponse,
    HyperviewTemplateView,
    InvalidTemplateName,
    SourceUnavailable,
    TemplateNotFound,
    TemplateResolver,
    TemplateValidationError,
)

_FULL_TEMPLATE = "screens/full.xml"
_FRAGMENT_TEMPLATE = "fragments/item.xml"
_FORM_TEMPLATE = "forms/submission.xml"
_FORM_RESULT_TEMPLATE = "fragments/submission.xml"


def _marker_headers(request: HttpRequest) -> dict[str, str]:
    details: HyperviewRequestDetails = request.hyperview
    return {
        "X-Consumer-Hyperview": str(bool(details)).lower(),
        "X-Consumer-Hyperview-Version": details.version or "",
    }


class ConsumerFullDocumentView(HyperviewTemplateView):
    """Render a generic full document through the public class-based view."""

    template_name = _FULL_TEMPLATE

    def get_context_data(self, **kwargs: object) -> dict[str, Any]:
        """Add consumer-owned request data to the template context.

        Args:
            **kwargs: Context values supplied by Django's view dispatch.

        Returns:
            Context containing the requested title.
        """
        context = super().get_context_data(**kwargs)
        context["title"] = self.request.GET.get("title", "Full document")
        return context

    def render_to_response(
        self, context: dict[str, Any], **response_kwargs: object
    ) -> HyperviewTemplateResponse:
        """Create the lazy full-document response with consumer metadata.

        Args:
            context: Template context prepared for the response.
            **response_kwargs: Response options supplied by Django.

        Returns:
            Lazy Hyperview template response.
        """
        return super().render_to_response(
            context,
            status=201,
            headers={"X-Consumer-Document": "full"},
            **response_kwargs,
        )


@require_GET
def consumer_fragment(request: HttpRequest) -> HyperviewResponse:
    """Render a generic fragment with public resolver and engine APIs.

    Args:
        request: Incoming Django request.

    Returns:
        Rendered Hyperview fragment response.

    Raises:
        HyperviewError: If the consumer template cannot be resolved or rendered.
    """
    resolver = TemplateResolver.from_settings()
    resolved = resolver.resolve(_FRAGMENT_TEMPLATE)
    content = HyperviewEngine(resolver).render_hxml(
        resolved.name,
        {"label": request.GET.get("label", "Fragment")},
        request,
    )
    return HyperviewResponse(
        content,
        status=206,
        headers={
            "X-Consumer-Document": "fragment",
            "X-Hyperview-Template": resolved.name,
        },
    )


@require_http_methods(["GET", "POST"])
def consumer_form(
    request: HttpRequest,
) -> HyperviewResponse | HyperviewTemplateResponse:
    """Render and submit a generic CSRF-protected consumer form.

    Args:
        request: Incoming Django request with Hyperview metadata.

    Returns:
        Lazy form document for GET or rendered confirmation for POST.

    Raises:
        HyperviewError: If a consumer template cannot be resolved or rendered.
    """
    headers = _marker_headers(request)
    if request.method == "GET":
        return HyperviewTemplateResponse(
            request,
            _FORM_TEMPLATE,
            headers=headers,
        )
    content = HyperviewEngine(TemplateResolver.from_settings()).render_hxml(
        _FORM_RESULT_TEMPLATE,
        {"message": request.POST.get("message", "")},
        request,
    )
    return HyperviewResponse(content, status=201, headers=headers)


_SOURCE_ERROR_BODY = "<view><text>request failed</text></view>"


def _source_error_response(error: Exception) -> HyperviewResponse:
    if isinstance(error, InvalidTemplateName):
        status, code = 400, "invalid_template_name"
    elif isinstance(error, TemplateNotFound):
        status, code = 404, "template_not_found"
    elif isinstance(error, TemplateValidationError):
        status, code = 422, error.code
    else:
        status, code = 503, "source_unavailable"
    return HyperviewResponse(
        _SOURCE_ERROR_BODY, status=status, headers={"X-Hyperview-Error": code}
    )


@require_GET
def consumer_source(request: HttpRequest) -> HyperviewResponse:
    """Render a named document through the configured public source stack.

    Args:
        request: Incoming request containing a template query parameter.

    Returns:
        Rendered document with safe source metadata or a redacted error response.
    """
    try:
        resolver = TemplateResolver.from_settings()
        resolved = resolver.resolve(request.GET.get("template", ""))
        content = HyperviewEngine(resolver).render_hxml(
            resolved.name, {"label": request.GET.get("label", "")}, request
        )
    except (
        InvalidTemplateName,
        SourceUnavailable,
        TemplateNotFound,
        TemplateValidationError,
    ) as error:
        return _source_error_response(error)
    return HyperviewResponse(
        content,
        headers={
            "X-Hyperview-Template": resolved.name,
            "X-Hyperview-Source": resolved.source,
            "X-Hyperview-Revision": resolved.revision,
        },
    )
