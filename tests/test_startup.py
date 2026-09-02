from django.core.checks import run_checks
from django.test import override_settings


@override_settings(HYPERVIEW="invalid")
def test_app_config_registers_hyperview_checks() -> None:
    errors = run_checks(tags=["dj_hyperview"])

    assert [error.id for error in errors] == ["dj_hyperview.E001"]


@override_settings(HYPERVIEW={})
def test_registered_check_accepts_minimal_installation() -> None:
    assert run_checks(tags=["dj_hyperview"]) == []
