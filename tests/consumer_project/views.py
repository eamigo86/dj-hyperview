"""Generic HTTP views for the package-owned Django consumer."""

from typing import Any

from django.http import HttpRequest
from django.views.decorators.http import require_GET

from dj_hyperview import (
    HyperviewEngine,
    HyperviewResponse,
    HyperviewTemplateResponse,
    HyperviewTemplateView,
    TemplateResolver,
)

_FULL_TEMPLATE = "screens/full.xml"
_FRAGMENT_TEMPLATE = "fragments/item.xml"


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
