"""Django application configuration for the core package."""

from importlib import import_module

from django.apps import AppConfig


class DjHyperviewConfig(AppConfig):
    """Register the reusable core application with Django."""

    name = "dj_hyperview"
    verbose_name = "Hyperview"

    def ready(self) -> None:
        """Load package system checks when the app registry is ready."""
        import_module("dj_hyperview.checks")
