"""Class-based views for Hyperview templates."""

from django.views.generic import TemplateView

from .http import HYPERVIEW_MEDIA_TYPE, HyperviewTemplateResponse


class HyperviewTemplateView(TemplateView):
    """Render a consumer-supplied template as a Hyperview response."""

    response_class = HyperviewTemplateResponse
    content_type = HYPERVIEW_MEDIA_TYPE
