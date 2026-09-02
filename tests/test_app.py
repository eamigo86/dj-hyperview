from importlib import import_module

from dj_hyperview.apps import DjHyperviewConfig


def test_app_config_uses_public_namespace() -> None:
    config = DjHyperviewConfig("dj_hyperview", import_module("dj_hyperview"))

    assert config.name == "dj_hyperview"
    assert config.verbose_name == "Hyperview"
