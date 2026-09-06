"""Hyperview-specific Django template tags."""

import warnings

from django import template
from django.conf import settings
from django.middleware.csrf import get_token
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag(takes_context=True)
def hv_csrf_token(context: template.RequestContext) -> SafeString:
    """Render Django's CSRF token as a hidden Hyperview form field.

    Args:
        context: Current template context.

    Returns:
        Escaped markup containing the hidden CSRF field.
    """
    request = getattr(context, "request", None)
    if request is None:
        if settings.DEBUG:
            warnings.warn(
                "{% hv_csrf_token %} was used in a template, but the context "
                "did not provide a request. This is usually caused by not using "
                "RequestContext.",
                stacklevel=2,
            )
        return SafeString("")
    return format_html(
        '<text-field hide="true" name="csrfmiddlewaretoken" value="{}" />',
        get_token(request),
    )
