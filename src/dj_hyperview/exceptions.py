"""Stable public exceptions raised by the package."""

from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured


class HyperviewError(Exception):
    """Base class for stable public dj-hyperview errors."""


class HyperviewConfigurationError(HyperviewError, ImproperlyConfigured):
    """The configured Hyperview settings are invalid."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"Invalid HYPERVIEW configuration: {'; '.join(issues)}")


class InvalidTemplateName(HyperviewError, ValueError):
    """A template name cannot be resolved safely."""

    def __init__(self, name: object) -> None:
        self.name = name
        super().__init__("Invalid template name")


class TemplateNotFound(HyperviewError, LookupError):
    """No configured source could resolve a template."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Template not found: {name}")


class TemplateValidationError(HyperviewError, ValueError):
    """HXML failed a deterministic validation rule."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"HXML validation failed [{code}]: {message}")


class SourceUnavailable(HyperviewError):
    """A configured template infrastructure source cannot be used safely."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Template source unavailable: {source} ({reason})")
