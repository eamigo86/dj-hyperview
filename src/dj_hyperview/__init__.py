"""Reusable Django infrastructure for Hyperview interfaces."""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _distribution_version

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
from .http import (
    HYPERVIEW_FRAGMENT_MEDIA_TYPE,
    HYPERVIEW_MEDIA_TYPE,
    HyperviewFragmentResponse,
    HyperviewFragmentTemplateResponse,
    HyperviewResponse,
    HyperviewTemplateResponse,
)
from .loaders import ResolverLoader
from .middleware import (
    HYPERVIEW_VERSION_HEADER,
    HyperviewMiddleware,
    HyperviewRequestDetails,
    detect_hyperview_request,
)
from .resolver import TemplateResolver, resolve_template
from .schema import HYPERVIEW_SCHEMA_VERSION
from .sources import FileSystemSource, ResolvedTemplate, TemplateSource
from .validation import validate_fragment_hxml, validate_hxml, validate_template_source
from .views import HyperviewTemplateView

try:
    __version__ = _distribution_version("dj-hyperview")
except _PackageNotFoundError:
    __version__ = "0.1.0a8"

__all__ = [
    "__version__",
    "HYPERVIEW_MEDIA_TYPE",
    "HYPERVIEW_FRAGMENT_MEDIA_TYPE",
    "HYPERVIEW_VERSION_HEADER",
    "HYPERVIEW_SCHEMA_VERSION",
    "CACHE_MISS",
    "CacheEntry",
    "FileSystemSource",
    "HyperviewEngine",
    "HyperviewConfigurationError",
    "HyperviewError",
    "HyperviewMiddleware",
    "HyperviewRequestDetails",
    "HyperviewResponse",
    "HyperviewFragmentResponse",
    "HyperviewFragmentTemplateResponse",
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
    "validate_fragment_hxml",
    "validate_template_source",
]
