"""Mandatory corrected XSD has no settings or direct-call bypass."""

from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest
from django.test import override_settings

import dj_hyperview
from dj_hyperview import validation
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.conf import ValidationSettings, get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError
from dj_hyperview.schema import get_hyperview_catalog

HV = "https://hyperview.org/hyperview"
VALID = f'<view xmlns="{HV}"><text>Hello &amp; safe</text></view>'
INVALID = f'<view xmlns="{HV}" invented="true"/>'


@pytest.mark.parametrize(
    "method", ["validate_hxml", "validate_fragment_hxml", "validate_rendered_hxml"]
)
@pytest.mark.parametrize("explicit", [False, True])
@override_settings(HYPERVIEW={})
def test_every_public_validation_path_enforces_builtin_xsd(method, explicit):
    kwargs = {"config": ValidationSettings()} if explicit else {}
    with pytest.raises(TemplateValidationError, match="schema"):
        getattr(validation, method)(INVALID, **kwargs)
    assert getattr(validation, method)(VALID, **kwargs) == VALID


@override_settings(HYPERVIEW={})
def test_corrected_registry_and_capability_are_automatic():
    assert dj_hyperview.HYPERVIEW_VALIDATION_CONTRACT == "automatic-xsd-v1"
    assert "HYPERVIEW_VALIDATION_CONTRACT" in dj_hyperview.__all__
    assert validation.validate_hxml(
        f'<text xmlns="{HV}" ellipsizeMode="tail" accessibilityRole="button"/>'
    )
    catalog = get_hyperview_catalog()
    assert catalog["catalog_format"] == 2
    assert "schema_profile" not in catalog
    assert not hasattr(get_settings(), "schema_profile")


@pytest.mark.parametrize(
    "setting",
    [
        {"SCHEMA_PROFILE": None},
        {"SCHEMA_PROFILE": "upstream-0.110.0"},
        {"SCHEMA_PROFILE": "compatible-0.110.0-r2"},
        {"VALIDATION": {"MODE": None}},
        {"VALIDATION": {"MODE": "publish"}},
        {"VALIDATION": {"MODE": "publish_and_render"}},
        {"VALIDATION": {"SCHEMA": None}},
        {"VALIDATION": {"SCHEMA": lambda value: True}},
        {"VALIDATION": {"SCHEMA": "dj_hyperview.validate_hyperview_schema"}},
    ],
)
def test_retired_settings_fail_explicitly_even_for_old_defaults(setting):
    with override_settings(HYPERVIEW=setting):
        assert any("removed" in item.msg.lower() for item in check_hyperview_settings())
        with pytest.raises(HyperviewConfigurationError):
            get_settings()


def test_validation_settings_only_expose_limits():
    assert {field.name for field in fields(ValidationSettings)} == {
        "max_bytes",
        "max_depth",
        "max_nodes",
    }
    for kwargs in ({"mode": "publish"}, {"schema": None}, {"schema": lambda _: True}):
        with pytest.raises(TypeError):
            ValidationSettings(**kwargs)


@pytest.mark.parametrize("name", ["max_bytes", "max_depth", "max_nodes"])
@pytest.mark.parametrize("value", [0, -1, True, None, "5", 2.5])
def test_direct_limits_require_positive_integers(name, value):
    with pytest.raises(ValueError, match=name):
        ValidationSettings(**{name: value})


def test_direct_depth_limit_remains_bounded():
    with pytest.raises(ValueError, match="max_depth"):
        ValidationSettings(max_depth=257)


@override_settings(HYPERVIEW={})
def test_source_safety_is_not_full_document_validation():
    source = "<view>{% if condition %}{{ value }}"
    assert validation.validate_template_source(source) == source
    with pytest.raises(TemplateValidationError, match="forbidden_declaration"):
        validation.validate_template_source("<!DOCTYPE view><view/>")


@override_settings(HYPERVIEW={})
def test_private_result_is_immutable_exact_and_contract_bound():
    result = validation._validate_hxml_result(VALID)
    assert result.text == VALID
    assert result.content == VALID.encode()
    assert validation._is_current_hxml_result(result)
    assert not validation._is_current_hxml_result(result, fragment=True)
    assert not validation._is_current_hxml_result(
        result, config=ValidationSettings(max_nodes=50)
    )
    with pytest.raises(FrozenInstanceError):
        result.text = "changed"
    with override_settings(
        HYPERVIEW={
            "SCHEMA_EXTENSIONS": {
                "ELEMENT_ATTRIBUTES": {"image": {"variant": {"TYPE": "string"}}}
            }
        }
    ):
        assert not validation._is_current_hxml_result(result)


@override_settings(HYPERVIEW={})
def test_result_and_direct_schema_parse_once(monkeypatch):
    original = validation._parse
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(validation, "_parse", counted)
    validation._validate_hxml_result(VALID, fragment=True)
    assert calls == [VALID]
    calls.clear()
    assert dj_hyperview.validate_hyperview_schema(VALID) is None
    assert calls == [VALID]


def test_private_handoff_rechecks_dependency_changes(tmp_path: Path):
    path = tmp_path / "extra.xsd"
    prefix = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="urn:extra">'
    )
    path.write_text(prefix + '<xs:element name="item"/></xs:schema>')
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [path]}):
        result = validation._validate_hxml_result(VALID)
        assert validation._is_current_hxml_result(result)
        path.write_text(prefix + '<xs:element name="changed-item"/></xs:schema>')
        assert not validation._is_current_hxml_result(result)
        path.write_text(
            prefix
            + '<xs:include schemaLocation="https://unsafe.test/x.xsd"/></xs:schema>'
        )
        with pytest.raises(TemplateValidationError, match="forbidden_schema_reference"):
            validation._is_current_hxml_result(result)


def test_compiled_registry_is_single_flight_across_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import sleep
    from types import SimpleNamespace

    from dj_hyperview import schema

    module = schema._xmlschema_module()
    calls = []
    barrier = Barrier(8)

    def compiler(*args, **kwargs):
        calls.append(args)
        sleep(0.01)
        return module.XMLSchema11(*args, **kwargs)

    with override_settings(HYPERVIEW={}):
        get_settings()
        monkeypatch.setattr(
            schema, "_xmlschema_module", lambda: SimpleNamespace(XMLSchema11=compiler)
        )

        def render(_):
            barrier.wait()
            return validation.validate_hxml(VALID)

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(render, range(8))) == [VALID] * 8
        assert len(calls) == 1


def test_schema_compilation_reused_until_setting_change(monkeypatch):
    from types import SimpleNamespace

    from dj_hyperview import schema

    module = schema._xmlschema_module()
    calls = []

    def compiler(*args, **kwargs):
        calls.append(args)
        return module.XMLSchema11(*args, **kwargs)

    with override_settings(HYPERVIEW={}):
        monkeypatch.setattr(
            schema, "_xmlschema_module", lambda: SimpleNamespace(XMLSchema11=compiler)
        )
        validation.validate_hxml(VALID)
        validation.validate_hxml(VALID)
        assert len(calls) == 1
        with override_settings(HYPERVIEW={"VALIDATION": {"MAX_NODES": 50}}):
            validation.validate_hxml(VALID)
            assert len(calls) == 2


def test_document_size_limit_does_not_limit_bundled_schema_size():
    assert (
        validation.validate_hxml(VALID, config=ValidationSettings(max_bytes=len(VALID)))
        == VALID
    )
