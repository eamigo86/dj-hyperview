from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured


class HyperviewError(Exception):
    """Base class for stable public dj-hyperview errors."""


class HyperviewConfigurationError(HyperviewError, ImproperlyConfigured):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"Invalid HYPERVIEW configuration: {'; '.join(issues)}")
