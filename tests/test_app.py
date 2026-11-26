"""The Django app configuration of dj-hyperview."""

from importlib import import_module

from dj_hyperview.apps import DjHyperviewConfig


def test_app_config_uses_public_namespace() -> None:
    """The app config carries the public package name and the Hyperview label."""
    config = DjHyperviewConfig("dj_hyperview", import_module("dj_hyperview"))

    assert config.name == "dj_hyperview"
    assert config.verbose_name == "Hyperview"
