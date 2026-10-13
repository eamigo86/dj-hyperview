"""Hyperview-specific Django template tags."""

import warnings

from django import template
from django.conf import settings
from django.middleware.csrf import get_token
from django.template.defaultfilters import stringfilter
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from dj_hyperview.validators import _XML_ILLEGAL_CHARACTERS

register = template.Library()
_ATTRIBUTE_WHITESPACE = str.maketrans({"\n": "&#10;", "\r": "&#13;", "\t": "&#9;"})


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


@register.filter(is_safe=True)
@stringfilter
def hv_text(value: str) -> SafeString:
    """Make text safe for Hyperview element content.

    Characters that XML 1.0 cannot represent are removed before the remaining
    text is escaped, so the result can be placed between tags.

    Args:
        value: Untrusted text to render.

    Returns:
        Escaped markup without XML-illegal characters.
    """
    return escape(_XML_ILLEGAL_CHARACTERS.sub("", value))


@register.filter(is_safe=True)
@stringfilter
def hv_attr(value: str) -> SafeString:
    """Make text safe for a Hyperview attribute value.

    It applies the same cleaning and escaping as the text filter, then encodes
    line feeds, carriage returns and tabs as character references. Without that
    step an XML parser normalizes them to spaces.

    Args:
        value: Untrusted text to render.

    Returns:
        Escaped markup that preserves attribute whitespace.
    """
    return mark_safe(hv_text(value).translate(_ATTRIBUTE_WHITESPACE))
