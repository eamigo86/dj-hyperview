"""Reusable Django infrastructure for Hyperview interfaces."""

from .cache import (
    CACHE_MISS,
    CacheEntry,
    TemplateCache,
    invalidate_templates,
    template_cache_key,
)
from .engine import HyperviewEngine, render_template
from .exceptions import (
    HyperviewConfigurationError,
    HyperviewError,
    InvalidTemplateName,
    SourceUnavailable,
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
from .validation import validate_hxml, validate_template_source
from .views import HyperviewTemplateView

__all__ = [
    "HYPERVIEW_MEDIA_TYPE",
    "HYPERVIEW_VERSION_HEADER",
    "CACHE_MISS",
    "CacheEntry",
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
    "SourceUnavailable",
    "TemplateCache",
    "TemplateNotFound",
    "TemplateResolver",
    "TemplateSource",
    "TemplateValidationError",
    "detect_hyperview_request",
    "invalidate_templates",
    "resolve_template",
    "render_template",
    "template_cache_key",
    "validate_hxml",
    "validate_template_source",
]
