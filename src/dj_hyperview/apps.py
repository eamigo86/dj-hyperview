from importlib import import_module

from django.apps import AppConfig


class DjHyperviewConfig(AppConfig):
    name = "dj_hyperview"
    verbose_name = "Hyperview"

    def ready(self) -> None:
        import_module("dj_hyperview.checks")
