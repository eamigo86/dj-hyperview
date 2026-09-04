"""Hyperview-specific Django template tags."""

from django import template
from django.middleware.csrf import get_token
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag(takes_context=True)
def hv_csrf_token(context: template.RequestContext) -> SafeString:
    """Render Django's CSRF token as a hidden Hyperview form field."""
    return format_html(
        '<text-field hide="true" name="csrfmiddlewaretoken" value="{}" />',
        get_token(context.request),
    )
