"""Reusable Django infrastructure for Hyperview interfaces."""

from .engine import HyperviewEngine, render_template
from .exceptions import (
    HyperviewConfigurationError,
    HyperviewError,
    InvalidTemplateName,
    TemplateNotFound,
    TemplateValidationError,
)
from .http import HYPERVIEW_MEDIA_TYPE, HyperviewResponse, HyperviewTemplateResponse
from .loaders import ResolverLoader
from .middleware import (
    HYPERVIEW_VERSION_HEADER,
    HyperviewMiddleware,
    HyperviewRequestDetails,
    detect_hyperview_request,
)
from .resolver import TemplateResolver, resolve_template
from .sources import FileSystemSource, ResolvedTemplate, TemplateSource
from .validation import validate_hxml
from .views import HyperviewTemplateView

__all__ = [
    "HYPERVIEW_MEDIA_TYPE",
    "HYPERVIEW_VERSION_HEADER",
    "FileSystemSource",
    "HyperviewEngine",
    "HyperviewConfigurationError",
    "HyperviewError",
    "HyperviewMiddleware",
    "HyperviewRequestDetails",
    "HyperviewResponse",
    "HyperviewTemplateResponse",
    "HyperviewTemplateView",
    "InvalidTemplateName",
    "ResolvedTemplate",
    "ResolverLoader",
    "TemplateNotFound",
    "TemplateResolver",
    "TemplateSource",
    "TemplateValidationError",
    "detect_hyperview_request",
    "resolve_template",
    "render_template",
    "validate_hxml",
]
