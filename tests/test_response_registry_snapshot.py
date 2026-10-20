"""One registry snapshot per response, and the commit checks that must remain.

The snapshot (`_get_registry_snapshot`) guards and fingerprints every configured
extra schema, so each call costs time in proportion to their number and size. A
response needs it once, to validate. A result that waits before it is committed
(a gzip handoff after middleware, content assigned later) is still checked against
the current contract.
"""

import gzip

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


def hyperview(extra_schema, **options):
    sources = [
        {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"content": DOCUMENT}}
    ]
    return {"EXTRA_SCHEMAS": [extra_schema], "SOURCES": sources, **options}


@pytest.fixture
def snapshots(extra_schema, monkeypatch):
    """Count the registry-state guards that run after the settings are ready."""
    with override_settings(TEMPLATES=TEMPLATES, HYPERVIEW=hyperview(extra_schema)):
        # The settings snapshot guards the schemas once, and the first response
        # builds the engine and compiles the registry: neither is under test.
        get_settings()
        HyperviewTemplateResponse(None, "screen.xml").render()
        calls = []
        original = schema._registry_state

        def counted(extra_schemas):
            calls.append(tuple(extra_schemas))
            return original(extra_schemas)

        monkeypatch.setattr(schema, "_registry_state", counted)
        yield calls


@pytest.mark.parametrize("using", [None, "django"])
@pytest.mark.parametrize(
    "response_type", [HyperviewTemplateResponse, HyperviewFragmentTemplateResponse]
)
def test_rendered_template_response_checks_the_snapshot_once(
    snapshots, using, response_type
):
    response = response_type(RequestFactory().get("/"), "screen.xml", using=using)
    response.render()
    assert response.content == DOCUMENT.encode()
    assert response.text == DOCUMENT
    assert bytes(response).endswith(DOCUMENT.encode())
    assert len(snapshots) == 1


def test_constructor_content_checks_the_snapshot_once(snapshots):
    assert HyperviewResponse(DOCUMENT).content == DOCUMENT.encode()
    assert len(snapshots) == 1


@pytest.mark.parametrize(
    "response_type", [HyperviewResponse, HyperviewFragmentResponse]
)
@pytest.mark.parametrize("action", ["assign", "write", "writelines"])
def test_body_mutation_checks_the_snapshot_once(snapshots, response_type, action):
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


def test_overridden_rendered_content_output_is_what_the_response_holds(snapshots):
    class Overriding(HyperviewTemplateResponse):
        @property
        def rendered_content(self):
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
def test_overridden_rendered_content_output_is_validated(snapshots, output, code):
    class Overriding(HyperviewTemplateResponse):
        @property
        def rendered_content(self):
            return output

    response = Overriding(RequestFactory().get("/"), "screen.xml", using="django")
    with pytest.raises(TemplateValidationError) as error:
        response.render()
    assert error.value.code == code
    assert not response.is_rendered


def test_gzip_handoff_checks_the_snapshot_once_more(snapshots):
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


def test_gzip_handoff_rechecks_a_changed_schema_dependency(snapshots, extra_schema):
    request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
    response = HyperviewResponse(DOCUMENT, request=request)
    extra_schema.write_text(STRICT_XSD, encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        GZipMiddleware(lambda request: response).process_response(request, response)
    assert error.value.code == "schema"
    assert response.content == DOCUMENT.encode()
    assert "Content-Encoding" not in response


def test_assigned_content_rechecks_limits_changed_before_commit(snapshots, monkeypatch):
    from dj_hyperview import http

    response = HyperviewResponse(DOCUMENT)
    original = http._validate_hxml_result
    changed = override_settings(HYPERVIEW={"VALIDATION": {"MAX_BYTES": 10}})
    enabled = []

    def validated_then_changed(*args, **kwargs):
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


def validated(document, **kwargs):
    """Validate as a response does: read the settings snapshot, then validate."""
    snapshot = get_settings()
    return validation._validate_hxml_result(document, **kwargs), snapshot


def test_commit_rechecks_a_result_validated_for_another_root_mode(snapshots):
    document = f'<doc xmlns="{HV}"><screen><body/></screen></doc>'
    result, under = validated(document)
    response = HyperviewFragmentResponse(OTHER_DOCUMENT)
    with pytest.raises(TemplateValidationError) as error:
        response._commit_result(result, under)
    assert error.value.code == "restricted_fragment_root"
    assert response.content == OTHER_DOCUMENT.encode()


def test_check_under_the_same_settings_does_not_guard_the_dependencies_again(
    snapshots, extra_schema
):
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
    snapshots, extra_schema
):
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
    snapshots, extra_schema
):
    result, under = validated(DOCUMENT)
    extra_schema.write_text(STRICT_XSD, encoding="utf-8")
    with override_settings(HYPERVIEW=hyperview(extra_schema)):
        assert not validation._is_current_hxml_result(result, validated_under=under)


def test_check_compares_the_limits_the_result_was_validated_with(snapshots):
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


def test_check_compares_the_root_mode_and_the_explicit_config(snapshots):
    result, under = validated(DOCUMENT)
    assert not validation._is_current_hxml_result(
        result, fragment=True, validated_under=under
    )
    narrow = ValidationSettings(max_nodes=50)
    assert not validation._is_current_hxml_result(
        result, config=narrow, validated_under=under
    )


def test_check_without_the_settings_it_was_validated_under_is_the_full_check(snapshots):
    result, _ = validated(DOCUMENT)
    snapshots.clear()
    assert validation._is_current_hxml_result(result)
    assert len(snapshots) == 1


def test_settings_that_change_while_content_validates_are_checked_at_commit(
    snapshots, extra_schema, monkeypatch
):
    """A change after the settings were read still needs the dependency guard."""
    from dj_hyperview import http

    response = HyperviewResponse(DOCUMENT)
    original = http._validate_hxml_result
    strict = extra_schema.with_name("strict.xsd")
    strict.write_text(STRICT_XSD, encoding="utf-8")
    changed = override_settings(HYPERVIEW=hyperview(strict))
    enabled = []

    def validated_then_changed(*args, **kwargs):
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
    snapshots, extra_schema, monkeypatch
):
    original = HyperviewTemplateResponse._rendered_result
    strict = extra_schema.with_name("strict.xsd")
    strict.write_text(STRICT_XSD, encoding="utf-8")
    changed = override_settings(HYPERVIEW=hyperview(strict))
    enabled = []

    def rendered_then_changed(self):
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
