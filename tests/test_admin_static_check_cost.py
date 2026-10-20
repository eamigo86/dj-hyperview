"""The Admin static check reads the cached catalog and asks the schema once per value.

Both savings are invisible to a verdict: the corpus below pins the diagnostics the
check reported before the catalog stopped being copied and attribute values started
being memoized, and the spies pin the cost.
"""

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any

import pytest
from django.test import override_settings

from dj_hyperview.contrib.database.admin_validation import (
    _static_schema_diagnostics,
    validate_draft_source,
)
from dj_hyperview.schema import _get_static_validation_catalog

HV = "https://hyperview.org/hyperview"
NAME = "screens/draft.xml"

EXTENSIONS = {
    "SCHEMA_EXTENSIONS": {
        "BEHAVIORS": {
            "show-snackbar": {
                "ATTRIBUTES": {
                    "message": {"TYPE": "string", "REQUIRED": True},
                    "tone": {"TYPE": "string", "ENUM": ["success", "error"]},
                }
            },
            "store-token": {
                "ATTRIBUTES": {"tone": {"TYPE": "string", "ENUM": ["bad", "worse"]}}
            },
        }
    }
}

# One draft per behaviour the memo must not change. Every invalid value is repeated
# on several elements, so each occurrence still has to be reported.
REPEATED_INVALID = f"""<doc xmlns="{HV}">
  <screen>
    <styles>
      <style id="a" width="bananas"/>
      <style id="b" width="bananas"/>
      <style id="c" width="50%"/>
      <style id="d" width="50%"/>
    </styles>
    <body>
      <view style="a" hide="maybe"/>
      <view style="b" hide="maybe"/>
      <view style="c" hide="true"/>
      <view style="d" hide="true"/>
      <text ellipsizeMode="tail">One</text>
      <text ellipsizeMode="tail">Two</text>
      <behavior trigger="press" action="alert" bogus="x"/>
      <behavior trigger="press" action="alert" bogus="x"/>
    </body>
  </screen>
</doc>"""

# "none" is a valid accessibilityRole and an invalid hide: the type is part of the key.
SAME_VALUE_DIFFERENT_TYPES = f"""<doc xmlns="{HV}">
  <screen>
    <body>
      <text accessibilityRole="none">a</text>
      <view hide="none"/>
      <text accessibilityRole="none">b</text>
      <view hide="none"/>
      <view accessibilityRole="none" hide="none"/>
    </body>
  </screen>
</doc>"""

# Values that carry a template token are skipped, and still are, next to an
# invalid static value that is repeated.
DYNAMIC_VALUES = f"""<doc xmlns="{HV}">
  <screen>
    <body>
      <view hide="{{{{ hidden }}}}"/>
      <view hide="{{{{ hidden }}}}"/>
      <view hide="maybe"/>
      <view hide="maybe"/>
      {{% if flag %}}<view hide="maybe"/>{{% endif %}}
    </body>
  </screen>
</doc>"""

REQUIRED_AND_UNKNOWN = f"""<doc xmlns="{HV}">
  <screen>
    <body>
      <text-field value="x"/>
      <text-field value="x"/>
      <text bogus="1" ellipsizeMode="tail"/>
      <text bogus="1" ellipsizeMode="tail"/>
      <unknown/>
    </body>
  </screen>
</doc>"""

VALID = f"""<doc xmlns="{HV}">
  <screen>
    <body>
      <view hide="true"/>
      <view hide="true"/>
      <text accessibilityRole="none">a</text>
    </body>
  </screen>
</doc>"""

# The same attribute name is typed differently by each registered action.
EXTENSION_BEHAVIORS = f"""<doc xmlns="{HV}">
  <screen>
    <body>
      <behavior action="show-snackbar" message="Hi" tone="bad"/>
      <behavior action="store-token" tone="bad"/>
      <behavior action="show-snackbar" message="Hi" tone="bad"/>
      <behavior action="store-token" tone="worse"/>
      <behavior action="show-snackbar" message="Hi" tone="error"/>
      <behavior action="store-token" tone="error"/>
    </body>
  </screen>
</doc>"""

CORPUS = {
    "repeated_invalid": (REPEATED_INVALID, None),
    "same_value_different_types": (SAME_VALUE_DIFFERENT_TYPES, None),
    "dynamic_values": (DYNAMIC_VALUES, None),
    "required_and_unknown": (REQUIRED_AND_UNKNOWN, None),
    "valid": (VALID, None),
    "extension_behaviors": (EXTENSION_BEHAVIORS, EXTENSIONS),
}


def _settings(extra: dict[str, Any] | None) -> Any:
    return override_settings(HYPERVIEW=extra or {})


def _diagnostic(code: str, line: int | None, message: str) -> dict[str, Any]:
    return {
        "severity": "info" if code == "schema_static_incomplete" else "error",
        "code": code,
        "message": message,
        "template": NAME,
        "coordinate_space": "source",
        "line": line,
        "column": None,
    }


def _value(attribute: str, element: str, line: int) -> dict[str, Any]:
    return _diagnostic(
        "schema_attribute_value",
        line,
        f'Attribute "{attribute}" on element "{element}" has a value not allowed '
        "by the selected schema.",
    )


def _unknown_attribute(attribute: str, element: str, line: int) -> dict[str, Any]:
    return _diagnostic(
        "schema_attribute",
        line,
        f'Attribute "{attribute}" is not allowed on element "{element}".',
    )


def _required(attribute: str, element: str, line: int) -> dict[str, Any]:
    return _diagnostic(
        "schema_required_attribute",
        line,
        f'Required attribute "{attribute}" is missing from element "{element}".',
    )


def _incomplete(line: int) -> dict[str, Any]:
    return _diagnostic(
        "schema_static_incomplete",
        line,
        "Static checks passed. Final HXML will be validated when served.",
    )


# Captured from the check before the catalog stopped being copied and the values
# memoized; the line is where the offending element starts.
GOLDEN: dict[str, list[dict[str, Any]]] = {
    "repeated_invalid": [
        _value("width", "style", 4),
        _value("width", "style", 5),
        _value("hide", "view", 10),
        _value("hide", "view", 11),
        _unknown_attribute("bogus", "behavior", 16),
        _unknown_attribute("bogus", "behavior", 17),
    ],
    "same_value_different_types": [
        _value("hide", "view", 5),
        _value("hide", "view", 7),
        _unknown_attribute("accessibilityRole", "view", 8),
        _value("hide", "view", 8),
    ],
    "dynamic_values": [
        _value("hide", "view", 6),
        _value("hide", "view", 7),
        _value("hide", "view", 8),
        _incomplete(8),
    ],
    "required_and_unknown": [
        _required("name", "text-field", 4),
        _required("name", "text-field", 5),
        _unknown_attribute("bogus", "text", 6),
        _unknown_attribute("bogus", "text", 7),
        _diagnostic(
            "schema_element",
            8,
            'Element "unknown" is not declared by the selected schema.',
        ),
        _diagnostic(
            "schema_structure",
            8,
            'Element "unknown" is not allowed inside "body" at this position.',
        ),
    ],
    "valid": [],
    "extension_behaviors": [
        _value("tone", "behavior", 4),
        _value("tone", "behavior", 6),
        _value("tone", "behavior", 9),
    ],
}


@pytest.fixture
def is_valid_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """Record the (type, value) pair of every simple type validity question."""
    from xmlschema.validators.simple_types import XsdSimpleType
    from xmlschema.validators.validation import ValidationMixin

    calls: list[tuple[int, str]] = []
    original = ValidationMixin.is_valid

    def spy(self: Any, obj: Any, *args: Any, **kwargs: Any) -> bool:
        if isinstance(self, XsdSimpleType):
            calls.append((id(self), obj))
        return original(self, obj, *args, **kwargs)

    monkeypatch.setattr(ValidationMixin, "is_valid", spy)
    return calls


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _walk(item)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def test_the_catalog_is_returned_without_a_copy(monkeypatch: pytest.MonkeyPatch):
    from dj_hyperview import schema

    with _settings(None):
        first = _get_static_validation_catalog()
        copies: list[object] = []
        monkeypatch.setattr(
            schema, "deepcopy", lambda value: copies.append(value) or value
        )
        second = _get_static_validation_catalog()
        _static_schema_diagnostics(NAME, REPEATED_INVALID)

    assert second is first
    assert copies == []


def test_the_cached_catalog_cannot_be_edited_at_any_depth():
    with _settings(EXTENSIONS):
        catalog = _get_static_validation_catalog()
        assert catalog["behavior_variants"]
        for node in _walk(catalog):
            assert type(node) is not dict
            assert not isinstance(node, (list, set))
        attributes = catalog["elements"]["text"]["attributes"]
        assert isinstance(attributes, MappingProxyType)
        with pytest.raises(TypeError):
            catalog["elements"] = {}
        with pytest.raises(TypeError):
            catalog["elements"]["text"]["namespace"] = "changed"
        with pytest.raises(TypeError):
            attributes["extra"] = {"enum": [], "required": False}
        with pytest.raises(TypeError):
            attributes["ellipsizeMode"]["required"] = True
        with pytest.raises(TypeError):
            del catalog["behavior_variants"]["show-snackbar"]["attributes"]["tone"]
        with pytest.raises(AttributeError):
            attributes["ellipsizeMode"]["enum"].append("changed")
        assert _get_static_validation_catalog() is catalog


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_checking_drafts_leaves_the_cached_catalog_unchanged(name: str):
    draft, extra = CORPUS[name]
    with _settings(extra):
        before = _thaw(_get_static_validation_catalog())
        _static_schema_diagnostics(NAME, draft)
        validate_draft_source(NAME, draft)

        assert _thaw(_get_static_validation_catalog()) == before


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_is_valid_runs_once_per_distinct_type_and_value(
    name: str, is_valid_calls: list[tuple[int, str]]
):
    draft, extra = CORPUS[name]
    with _settings(extra):
        _static_schema_diagnostics(NAME, draft)

    assert len(is_valid_calls) == len(set(is_valid_calls))


def test_is_valid_asks_one_question_per_distinct_type_and_value(
    is_valid_calls: list[tuple[int, str]],
):
    with _settings(None):
        _static_schema_diagnostics(NAME, REPEATED_INVALID)

    # width: bananas, 50%; hide: maybe, true; ellipsizeMode: tail; trigger: press;
    # action: alert; the ids and the style references that name them are two types.
    asked = sorted(value for _type, value in is_valid_calls)
    assert asked == sorted(
        ["bananas", "50%", "maybe", "true", "tail", "press", "alert"]
        + ["a", "b", "c", "d"] * 2
    )


def test_the_memo_is_per_call_and_keeps_no_state_between_calls(
    is_valid_calls: list[tuple[int, str]],
):
    with _settings(None):
        _static_schema_diagnostics(NAME, REPEATED_INVALID)
        first = list(is_valid_calls)
        is_valid_calls.clear()
        _static_schema_diagnostics(NAME, REPEATED_INVALID)

    assert first
    assert sorted(is_valid_calls) == sorted(first)


def test_the_memo_does_not_cross_types_that_share_a_value(
    is_valid_calls: list[tuple[int, str]],
):
    with _settings(None):
        diagnostics = _static_schema_diagnostics(NAME, SAME_VALUE_DIFFERENT_TYPES)

    none_types = {kind for kind, value in is_valid_calls if value == "none"}
    assert len(none_types) == 2
    assert [(item["code"], item["line"]) for item in diagnostics] == [
        ("schema_attribute_value", 5),
        ("schema_attribute_value", 7),
        ("schema_attribute", 8),
        ("schema_attribute_value", 8),
    ]


def test_a_value_that_is_not_checked_is_not_asked(
    is_valid_calls: list[tuple[int, str]],
):
    with _settings(None):
        _static_schema_diagnostics(NAME, DYNAMIC_VALUES)

    assert [value for _type, value in is_valid_calls] == ["maybe"]


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_diagnostics_are_the_ones_the_check_reported_before(name: str):
    draft, extra = CORPUS[name]
    with _settings(extra):
        assert _static_schema_diagnostics(NAME, draft) == GOLDEN[name]
        assert validate_draft_source(NAME, draft)["diagnostics"] == GOLDEN[name]
