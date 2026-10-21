"""Real installed xmlschema boundary checks, run in an isolated CI environment."""

import importlib.metadata
import os

import pytest
import xmlschema
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from dj_hyperview import schema
from dj_hyperview._schema_extensions import _EMPTY_EXTENSIONS
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.validation import validate_hxml

HV = "https://hyperview.org/hyperview"


def test_installed_release_is_the_selected_compatibility_boundary():
    target = os.environ.get("DJHV_XMLSCHEMA_COMPAT_VERSION")
    actual = importlib.metadata.version("xmlschema")

    assert actual == xmlschema.__version__
    assert Version(actual) in SpecifierSet(">=4.3,<5")
    if target == "4.3.0":
        assert actual == "4.3.0"
        registry = schema._cached_registry.__wrapped__((), _EMPTY_EXTENSIONS)
        assert type(registry) is xmlschema.XMLSchema11
    elif target == "latest":
        assert not Version(actual).is_prerelease
    else:
        assert target is None, "CI must select an explicit compatibility boundary"


def test_public_validator_keeps_real_xsd_11_verdicts():
    valid = f'<doc xmlns="{HV}"><screen><body /></screen></doc>'
    assert validate_hxml(valid) == valid
    with pytest.raises(TemplateValidationError) as error:
        validate_hxml(f'<nav-route xmlns="{HV}" id="missing-destination" />')
    assert error.value.code == "schema"
