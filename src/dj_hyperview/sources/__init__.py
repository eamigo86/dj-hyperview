"""Template source interfaces and implementations."""

from .base import ResolvedTemplate, TemplateSource, canonicalize_template_name
from .filesystem import FileSystemSource

__all__ = [
    "FileSystemSource",
    "ResolvedTemplate",
    "TemplateSource",
    "canonicalize_template_name",
]
