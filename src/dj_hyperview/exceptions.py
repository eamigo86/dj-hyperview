"""Stable public exceptions raised by the package."""

from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured


class HyperviewError(Exception):
    """Base class for stable public dj-hyperview errors."""


class HyperviewConfigurationError(HyperviewError, ImproperlyConfigured):
    """The configured Hyperview settings are invalid."""

    def __init__(self, issues: Sequence[str]) -> None:
        """Initialize a configuration error.

        Args:
            issues: Stable configuration issue descriptions.
        """
        self.issues = tuple(issues)
        super().__init__(f"Invalid HYPERVIEW configuration: {'; '.join(issues)}")


class InvalidTemplateName(HyperviewError, ValueError):
    """A template name cannot be resolved safely."""

    def __init__(self, name: object) -> None:
        """Initialize a redacted invalid-name error.

        Args:
            name: Rejected name retained for programmatic inspection.
        """
        self.name = name
        super().__init__("Invalid template name")


class TemplateNotFound(HyperviewError, LookupError):
    """No configured source could resolve a template."""

    def __init__(self, name: str) -> None:
        """Initialize a final template miss.

        Args:
            name: Canonical template name that was not resolved.
        """
        self.name = name
        super().__init__(f"Template not found: {name}")


class TemplateValidationError(HyperviewError, ValueError):
    """HXML failed a deterministic validation rule."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize a deterministic template validation error.

        Args:
            code: Stable validation error code.
            message: Safe validation failure description.
        """
        self.code = code
        self.message = message
        super().__init__(f"HXML validation failed [{code}]: {message}")


class SourceUnavailable(HyperviewError):
    """A configured template infrastructure source cannot be used safely."""

    def __init__(self, source: str, reason: str) -> None:
        """Initialize a redacted source availability error.

        Args:
            source: Stable source identity.
            reason: Safe availability failure description.
        """
        self.source = source
        self.reason = reason
        super().__init__(f"Template source unavailable: {source} ({reason})")
