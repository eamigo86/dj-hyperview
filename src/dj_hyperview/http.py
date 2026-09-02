"""HTTP responses for Hyperview markup."""

from django.http import HttpResponse
from django.template.response import TemplateResponse

HYPERVIEW_MEDIA_TYPE = "application/vnd.hyperview+xml"


class HyperviewResponse(HttpResponse):
    """An HTTP response that defaults to the Hyperview media type."""

    def __init__(self, content=b"", *, content_type=HYPERVIEW_MEDIA_TYPE, **kwargs):
        super().__init__(content, content_type=content_type, **kwargs)


class HyperviewTemplateResponse(TemplateResponse):
    """A lazily rendered Django template response for Hyperview markup."""

    def __init__(
        self,
        request,
        template,
        context=None,
        content_type=HYPERVIEW_MEDIA_TYPE,
        status=None,
        charset=None,
        using=None,
        headers=None,
    ):
        super().__init__(
            request=request,
            template=template,
            context=context,
            content_type=content_type,
            status=status,
            charset=charset,
            using=using,
            headers=headers,
        )
