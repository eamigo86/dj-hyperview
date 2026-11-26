"""Compiled registries and catalogs outlive a change of the HYPERVIEW setting.

A test suite that enters override_settings with the same value must not compile
the schema again, and one that changes the schema settings must still get the
registry of the new settings.
"""

from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings
from django.test.signals import setting_changed

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.schema import (
    _active_catalog,
    _cached_registry,
    _cached_static_validation_catalog,
    _get_compiled_registry,
    _get_static_validation_catalog,
    get_hyperview_catalog,
)

HV = "https://hyperview.org/hyperview"
NS = "urn:test:registry-cache"
CACHES = (_cached_registry, _cached_static_validation_catalog, _active_catalog)


def schema_file(path: Path, element: str) -> Path:
    path.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        f'targetNamespace="{NS}" elementFormDefault="qualified">'
        f'<xs:element name="{element}"><xs:complexType>'
        '<xs:attribute name="id" use="required"/></xs:complexType></xs:element>'
        "</xs:schema>",
        encoding="utf-8",
    )
    return path


def document(element: str) -> str:
    return f'<view xmlns="{HV}" xmlns:app="{NS}"><app:{element}/></view>'


def accepts(content: str) -> bool:
    try:
        validate_hyperview_schema(content)
    except TemplateValidationError:
        return False
    return True


def declares(element: str) -> bool:
    """Whether the registry knows the element: only then is a missing id an error."""
    return not accepts(document(element))


def extensions(tones: list[str]) -> dict[str, Any]:
    return {
        "SCHEMA_EXTENSIONS": {
            "BEHAVIORS": {
                "show-snackbar": {
                    "ATTRIBUTES": {"tone": {"TYPE": "string", "ENUM": tones}}
                }
            }
        }
    }


def snackbar(tone: str) -> str:
    return f'<view xmlns="{HV}"><behavior action="show-snackbar" tone="{tone}"/></view>'


def misses() -> tuple[int, ...]:
    return tuple(cache.cache_info().misses for cache in CACHES)


def warm() -> None:
    _get_compiled_registry()
    _get_static_validation_catalog()
    get_hyperview_catalog()


def test_the_same_settings_keep_every_compiled_result_across_an_override(
    tmp_path: Path,
) -> None:
    value = {"EXTRA_SCHEMAS": [schema_file(tmp_path / "app.xsd", "alpha")]}
    base = _get_compiled_registry()
    with override_settings(HYPERVIEW=value):
        warm()
        first = _get_compiled_registry()
        static = _get_static_validation_catalog()
        compiled = misses()

    with override_settings(HYPERVIEW=dict(value)):
        warm()
        assert _get_compiled_registry() is first
        assert _get_static_validation_catalog() is static
        assert misses() == compiled

    assert _get_compiled_registry() is base
    with override_settings(HYPERVIEW=dict(value)):
        warm()
        assert misses() == compiled


def test_entering_the_same_empty_override_compiles_nothing() -> None:
    base = _get_compiled_registry()
    warm()
    before = misses()

    with override_settings(HYPERVIEW={}):
        warm()
        assert _get_compiled_registry() is base

    warm()
    assert misses() == before


def test_each_extra_schema_selects_its_own_registry(tmp_path: Path) -> None:
    first = schema_file(tmp_path / "first.xsd", "alpha")
    second = schema_file(tmp_path / "second.xsd", "beta")
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [first]}):
        alpha = _get_compiled_registry()
        assert declares("alpha")
        assert not declares("beta")
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [second]}):
        beta = _get_compiled_registry()
        assert beta is not alpha
        assert declares("beta")
        assert not declares("alpha")
        assert f"{{{NS}}}beta" in get_hyperview_catalog()["elements"]
        assert f"{{{NS}}}alpha" not in _get_static_validation_catalog()["elements"]
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [first]}):
        assert _get_compiled_registry() is alpha
        assert declares("alpha")
        assert not declares("beta")
        assert f"{{{NS}}}alpha" in _get_static_validation_catalog()["elements"]
        assert f"{{{NS}}}beta" not in get_hyperview_catalog()["elements"]


def test_each_schema_extension_selects_its_own_registry() -> None:
    with override_settings(HYPERVIEW=extensions(["calm", "loud"])):
        calm = _get_compiled_registry()
        assert accepts(snackbar("calm"))
        assert not accepts(snackbar("quiet"))
    with override_settings(HYPERVIEW=extensions(["quiet"])):
        assert _get_compiled_registry() is not calm
        assert accepts(snackbar("quiet"))
        assert not accepts(snackbar("calm"))
        assert get_hyperview_catalog()["behavior_variants"]["show-snackbar"][
            "attributes"
        ]["tone"]["enum"] == ["quiet"]
        assert _get_static_validation_catalog()["behavior_variants"]["show-snackbar"][
            "attributes"
        ]["tone"]["enum"] == ("quiet",)
    with override_settings(HYPERVIEW=extensions(["calm", "loud"])):
        assert _get_compiled_registry() is calm
        assert accepts(snackbar("loud"))
        assert not accepts(snackbar("quiet"))


def test_a_schema_file_rewritten_between_overrides_is_not_served_from_the_cache(
    tmp_path: Path,
) -> None:
    path = schema_file(tmp_path / "app.xsd", "alpha")
    value = {"EXTRA_SCHEMAS": [path]}
    with override_settings(HYPERVIEW=value):
        before = _get_compiled_registry()
        assert declares("alpha")

    schema_file(path, "a-longer-element-name")

    with override_settings(HYPERVIEW=value):
        assert _get_compiled_registry() is not before
        assert declares("a-longer-element-name")
        assert not declares("alpha")
        assert f"{{{NS}}}a-longer-element-name" in get_hyperview_catalog()["elements"]


def test_validation_limits_follow_the_override_while_the_registry_is_shared() -> None:
    content = f'<view xmlns="{HV}"><text/><text/></view>'
    base = _get_compiled_registry()
    assert accepts(content)

    with override_settings(HYPERVIEW={"VALIDATION": {"MAX_NODES": 2}}):
        assert _get_compiled_registry() is base
        assert not accepts(content)

    assert accepts(content)


def test_a_setting_change_empties_none_of_the_schema_caches(tmp_path: Path) -> None:
    value = {"EXTRA_SCHEMAS": [schema_file(tmp_path / "app.xsd", "alpha")]}
    with override_settings(HYPERVIEW=value):
        warm()
        sizes = [cache.cache_info().currsize for cache in CACHES]
        assert all(sizes)

        setting_changed.send(
            sender=object, setting="HYPERVIEW", value=value, enter=True
        )

        assert [cache.cache_info().currsize for cache in CACHES] == sizes


@pytest.mark.parametrize("cache", CACHES, ids=lambda cache: cache.__wrapped__.__name__)
def test_the_kept_results_stay_bounded(cache: Any) -> None:
    assert cache.cache_info().maxsize == 64
