"""One registry snapshot per response, and the commit checks that must remain.

The snapshot (_get_registry_snapshot) guards and fingerprints every configured
extra schema, so each call costs time in proportion to their number and size. A
response needs it once, to validate. A result that waits before it is committed
(a gzip handoff after middleware, content assigned later) is still checked against
the current contract.
"""

import gzip
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.middleware.gzip import GZipMiddleware
from django.test import RequestFactory, override_settings

from dj_hyperview import schema, validation
from dj_hyperview.conf import ValidationSettings, get_settings
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import (
    HyperviewFragmentResponse,
    HyperviewFragmentTemplateResponse,
    HyperviewResponse,
    HyperviewTemplateResponse,
)
from tests.response_documents import APP, DOCUMENT, HV, STRICT_XSD, TEMPLATES

OTHER_DOCUMENT = (
    f'<view xmlns="{HV}" xmlns:app="{APP}"><app:chip>other</app:chip></view>'
)
COMMENT = "<!-- appended -->"


def hyperview(extra_schema: Path, **options: Any) -> dict[str, Any]:
    """Build a HYPERVIEW setting with the extra schema and a stub source.

    Args:
        extra_schema: Path of the extra schema that the settings load.
        **options: Further HYPERVIEW settings.

    Returns:
        A HYPERVIEW setting with the extra schema and a stub source.
    """
    sources = [
        {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"content": DOCUMENT}}
    ]
    return {"EXTRA_SCHEMAS": [extra_schema], "SOURCES": sources, **options}


@pytest.fixture
def snapshots(
    extra_schema: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[tuple[Path, ...]]]:
    """Count the registry-state guards that run after the settings are ready.

    Args:
        extra_schema: Path of the extra schema that the settings load.
        monkeypatch: Patches the code under test for this test.

    Yields:
        The extra schemas of each registry-state guard that ran.
    """
    with override_settings(TEMPLATES=TEMPLATES, HYPERVIEW=hyperview(extra_schema)):
        # The settings snapshot guards the schemas once, and the first response
        # builds the engine and compiles the registry: neither is under test.
        get_settings()
        HyperviewTemplateResponse(None, "screen.xml").render()
        calls = []
        original = schema._registry_state

        def counted(extra_schemas: Iterable[Path]) -> Any:
            calls.append(tuple(extra_schemas))
            return original(extra_schemas)

        monkeypatch.setattr(schema, "_registry_state", counted)
        yield calls


@pytest.mark.parametrize("using", [None, "django"])
@pytest.mark.parametrize(
    "response_type", [HyperviewTemplateResponse, HyperviewFragmentTemplateResponse]
)
def test_rendered_template_response_checks_the_snapshot_once(
    snapshots: list[tuple[Path, ...]], using: str | None, response_type: type
) -> None:
    """Rendered template response checks the snapshot once.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        using: Template engine alias, or None for the default.
        response_type: Response class under test.
    """
    response = response_type(RequestFactory().get("/"), "screen.xml", using=using)
    response.render()
    assert response.content == DOCUMENT.encode()
    assert response.text == DOCUMENT
    assert bytes(response).endswith(DOCUMENT.encode())
    assert len(snapshots) == 1


def test_constructor_content_checks_the_snapshot_once(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Constructor content checks the snapshot once.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    assert HyperviewResponse(DOCUMENT).content == DOCUMENT.encode()
    assert len(snapshots) == 1


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("action", ["assign", "write", "writelines"])
def test_body_mutation_checks_the_snapshot_once(
    snapshots: list[tuple[Path, ...]], response_type: type, action: str
) -> None:
    """Body mutation checks the snapshot once.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        response_type: Response class under test.
        action: How the body is changed: assign, write or writelines.
    """
    response = response_type(OTHER_DOCUMENT)
    snapshots.clear()
    if action == "assign":
        response.content = DOCUMENT
    elif action == "write":
        response.write(COMMENT)
    else:
        response.writelines([COMMENT])
    expected = DOCUMENT if action == "assign" else OTHER_DOCUMENT + COMMENT
    assert response.content == expected.encode()
    assert len(snapshots) == 1


def test_overridden_rendered_content_output_is_what_the_response_holds(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Overridden rendered content output is what the response holds.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """

    class Overriding(HyperviewTemplateResponse):
        @property
        def rendered_content(self) -> str:
            return OTHER_DOCUMENT

    response = Overriding(RequestFactory().get("/"), "screen.xml", using="django")
    response.render()
    assert response.content == OTHER_DOCUMENT.encode()


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("<view", "malformed_xml"),
        (f'<view xmlns="{HV}"><unknown/></view>', "schema"),
    ],
)
def test_overridden_rendered_content_output_is_validated(
    snapshots: list[tuple[Path, ...]], output: str, code: str
) -> None:
    """Overridden rendered content output is validated.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        output: Document that the overridden rendered content returns.
        code: Validation error code that the document produces.
    """

    class Overriding(HyperviewTemplateResponse):
        @property
        def rendered_content(self) -> str:
            return output

    response = Overriding(RequestFactory().get("/"), "screen.xml", using="django")
    with pytest.raises(TemplateValidationError) as error:
        response.render()
    assert error.value.code == code
    assert not response.is_rendered


def test_gzip_handoff_checks_the_snapshot_once_more(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Gzip handoff checks the snapshot once more.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    response = HyperviewTemplateResponse(request, "screen.xml", using="django")
    response.render()
    assert len(snapshots) == 1
    GZipMiddleware(lambda request: response).process_response(request, response)
    assert response["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.content) == DOCUMENT.encode()
    # The compressed body meets the result after the middleware stack: it crosses
    # time, so the contract is checked again.
    assert len(snapshots) == 2


def test_gzip_handoff_rechecks_a_changed_schema_dependency(
    snapshots: list[tuple[Path, ...]], extra_schema: Path
) -> None:
    """Gzip handoff rechecks a changed schema dependency.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
    """
    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    response = HyperviewResponse(DOCUMENT, request=request)
    extra_schema.write_text(STRICT_XSD, encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        GZipMiddleware(lambda request: response).process_response(request, response)
    assert error.value.code == "schema"
    assert response.content == DOCUMENT.encode()
    assert "Content-Encoding" not in response


def test_assigned_content_rechecks_limits_changed_before_commit(
    snapshots: list[tuple[Path, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assigned content rechecks limits changed before commit.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        monkeypatch: Patches the code under test for this test.
    """
    from dj_hyperview import http

    response = HyperviewResponse(DOCUMENT)
    original = http._validate_hxml_result
    changed = override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10}})
    enabled = []

    def validated_then_changed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not enabled:
            enabled.append(changed.enable())
        return result

    monkeypatch.setattr(http, "_validate_hxml_result", validated_then_changed)
    try:
        with pytest.raises(TemplateValidationError) as error:
            response.content = OTHER_DOCUMENT
    finally:
        if enabled:
            changed.disable()
    assert error.value.code == "max_bytes"
    assert response.content == DOCUMENT.encode()


def validated(document: str, **kwargs: Any) -> tuple[Any, Any]:
    """Validate as a response does: read the settings snapshot, then validate.

    Args:
        document: Document to validate.
        **kwargs: Keyword arguments passed through.

    Returns:
        The validation result and the settings snapshot that was read first.
    """
    snapshot = get_settings()
    return validation._validate_hxml_result(document, **kwargs), snapshot


def test_commit_rechecks_a_result_validated_for_another_root_mode(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Commit rechecks a result validated for another root mode.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    document = f'<doc xmlns="{HV}"><screen><body/></screen></doc>'
    result, under = validated(document)
    response = HyperviewFragmentResponse(OTHER_DOCUMENT)
    with pytest.raises(TemplateValidationError) as error:
        response._commit_result(result, under)
    assert error.value.code == "restricted_fragment_root"
    assert response.content == OTHER_DOCUMENT.encode()


def test_check_under_the_same_settings_does_not_guard_the_dependencies_again(
    snapshots: list[tuple[Path, ...]], extra_schema: Path
) -> None:
    """Check under the same settings does not guard the dependencies again.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
    """
    result, under = validated(DOCUMENT)
    snapshots.clear()
    assert validation._is_current_hxml_result(result, validated_under=under)
    assert snapshots == []
    # A result from the same call chain was guarded an instant ago, so a changed
    # file is not what this check looks for. The default check still is.
    extra_schema.write_text(STRICT_XSD, encoding="utf-8")
    assert validation._is_current_hxml_result(result, validated_under=under)
    assert not validation._is_current_hxml_result(result)


def test_check_falls_back_to_the_full_check_after_a_settings_change(
    snapshots: list[tuple[Path, ...]], extra_schema: Path
) -> None:
    """Check falls back to the full check after a settings change.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
    """
    result, under = validated(DOCUMENT)
    changed = hyperview(extra_schema, VALIDATION={"MAX_NODES": 50})
    with override_settings(HYPERVIEW=changed):
        assert not validation._is_current_hxml_result(result, validated_under=under)
    # Equal settings behind a new snapshot object are the same contract, and only
    # the full check can tell: it guards the dependencies.
    with override_settings(HYPERVIEW=hyperview(extra_schema)):
        # Building the settings snapshot guards the schemas as well, so warm it
        # first: what is counted below is the check itself.
        get_settings()
        snapshots.clear()
        assert validation._is_current_hxml_result(result, validated_under=under)
        assert len(snapshots) == 1


def test_check_guards_the_dependencies_when_the_settings_changed(
    snapshots: list[tuple[Path, ...]], extra_schema: Path
) -> None:
    """Check guards the dependencies when the settings changed.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
    """
    result, under = validated(DOCUMENT)
    extra_schema.write_text(STRICT_XSD, encoding="utf-8")
    with override_settings(HYPERVIEW=hyperview(extra_schema)):
        assert not validation._is_current_hxml_result(result, validated_under=under)


def test_check_compares_the_limits_the_result_was_validated_with(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Check compares the limits the result was validated with.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    narrow = ValidationSettings(max_nodes=50)
    result, under = validated(DOCUMENT, config=narrow)
    assert not validation._is_current_hxml_result(result, validated_under=under)
    assert validation._is_current_hxml_result(
        result, config=narrow, validated_under=under
    )
    explicit, under = validated(DOCUMENT, config=get_settings().validation)
    snapshots.clear()
    assert validation._is_current_hxml_result(explicit, validated_under=under)
    assert snapshots == []


def test_check_compares_the_root_mode_and_the_explicit_config(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Check compares the root mode and the explicit config.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    result, under = validated(DOCUMENT)
    assert not validation._is_current_hxml_result(
        result, fragment=True, validated_under=under
    )
    narrow = ValidationSettings(max_nodes=50)
    assert not validation._is_current_hxml_result(
        result, config=narrow, validated_under=under
    )


def test_check_without_the_settings_it_was_validated_under_is_the_full_check(
    snapshots: list[tuple[Path, ...]],
) -> None:
    """Check without the settings it was validated under is the full check.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
    """
    result, _ = validated(DOCUMENT)
    snapshots.clear()
    assert validation._is_current_hxml_result(result)
    assert len(snapshots) == 1


def test_settings_that_change_while_content_validates_are_checked_at_commit(
    snapshots: list[tuple[Path, ...]],
    extra_schema: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change after the settings were read still needs the dependency guard.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
        monkeypatch: Patches the code under test for this test.
    """
    from dj_hyperview import http

    response = HyperviewResponse(DOCUMENT)
    original = http._validate_hxml_result
    strict = extra_schema.with_name("strict.xsd")
    strict.write_text(STRICT_XSD, encoding="utf-8")
    changed = override_settings(HYPERVIEW=hyperview(strict))
    enabled = []

    def validated_then_changed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not enabled:
            enabled.append(changed.enable())
        return result

    monkeypatch.setattr(http, "_validate_hxml_result", validated_then_changed)
    try:
        with pytest.raises(TemplateValidationError) as error:
            response.content = DOCUMENT
    finally:
        if enabled:
            changed.disable()
    assert error.value.code == "schema"
    assert response.content == DOCUMENT.encode()


def test_settings_that_change_while_a_template_renders_are_checked_at_commit(
    snapshots: list[tuple[Path, ...]],
    extra_schema: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings that change while a template renders are checked at commit.

    Args:
        snapshots: Registry-state guards that ran after the settings were ready.
        extra_schema: Path of the extra schema that the settings load.
        monkeypatch: Patches the code under test for this test.
    """
    original = HyperviewTemplateResponse._rendered_result
    strict = extra_schema.with_name("strict.xsd")
    strict.write_text(STRICT_XSD, encoding="utf-8")
    changed = override_settings(HYPERVIEW=hyperview(strict))
    enabled = []

    def rendered_then_changed(self: Any) -> Any:
        result = original(self)
        if not enabled:
            enabled.append(changed.enable())
        return result

    monkeypatch.setattr(
        HyperviewTemplateResponse, "_rendered_result", rendered_then_changed
    )
    response = HyperviewTemplateResponse(
        RequestFactory().get("/"), "screen.xml", using="django"
    )
    try:
        with pytest.raises(TemplateValidationError) as error:
            response.render()
    finally:
        if enabled:
            changed.disable()
    assert error.value.code == "schema"
    assert not response.is_rendered
