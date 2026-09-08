"""Unpublished previews render isolated drafts and redact failures."""

import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings

from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError
from tests.test_engine import MemorySource

NS = "https://hyperview.org/hyperview"
VIEW = f'<view xmlns="{NS}"><text>{{{{ title }}}}</text></view>'


@pytest.fixture(autouse=True)
def preview_settings():
    apps = list(settings.INSTALLED_APPS)
    if "django_ace" not in apps:
        apps.append("django_ace")
    with override_settings(INSTALLED_APPS=apps):
        with override_settings(HYPERVIEW=config()):
            yield


def config(*, context=None, templates=None, root=None, validation=None, **extra):
    scenario = {
        "LABEL": "Example",
        "CONTEXT": context if context is not None else {"title": "A & B"},
    }
    if root:
        scenario["ROOT_TEMPLATE"] = root
    return {
        "ADMIN": {
            "EDITOR": True,
            "PREVIEW": {"ENABLED": True, "SCENARIOS": {"example": scenario}},
        },
        "SOURCES": [
            {
                "BACKEND": "tests.test_engine.MemorySource",
                "OPTIONS": {"templates": templates or {}},
            }
        ],
        "VALIDATION": validation or {},
        **extra,
    }


def preview(content=VIEW, name="draft.xml", scenario="example", **kwargs):
    from dj_hyperview.preview import render_preview

    return render_preview(name, content, scenario, **kwargs)


def error(result, code, space=None):
    assert result["ok"] is False, result
    if space != "rendered":
        assert result["hxml"] is None
    diagnostic = next(
        item for item in result["diagnostics"] if item["severity"] == "error"
    )
    assert diagnostic["code"] == code, result
    assert diagnostic["coordinate_space"] == space
    assert set(diagnostic) == {
        "severity",
        "code",
        "message",
        "template",
        "coordinate_space",
        "line",
        "column",
    }
    return diagnostic


def test_renders_unsaved_original_with_escaping_without_raw_cache():
    with override_settings(HYPERVIEW=config(CACHE={"TTL": 100})):
        with patch(
            "dj_hyperview.cache.TemplateCache.from_settings",
            side_effect=AssertionError("cache initialized"),
        ):
            with patch(
                "django.core.cache.backends.locmem.LocMemCache.set",
                side_effect=AssertionError("cache write"),
            ):
                result = preview("\n" + VIEW)
    assert result == {
        "ok": True,
        "hxml": f'\n<view xmlns="{NS}"><text>A &amp; B</text></view>',
        "diagnostics": [],
    }


def test_draft_overrides_saved_source_and_requests_are_isolated():
    with override_settings(
        HYPERVIEW=config(
            templates={"draft.xml": VIEW.replace("{{ title }}", "published")}
        )
    ):
        assert "first" in preview(VIEW.replace("{{ title }}", "first"))["hxml"]
        assert "second" in preview(VIEW.replace("{{ title }}", "second"))["hxml"]
        assert "published" not in preview()["hxml"]


def test_dynamic_include_and_extends_use_configured_sources():
    templates = {
        "base.xml": f'<view xmlns="{NS}">{{% block main %}}{{% endblock %}}</view>',
        "part.xml": "<text>{{ title }}</text>",
    }
    draft = '{% extends "base.xml" %}{% block main %}{% include part %}{% endblock %}'
    with override_settings(
        HYPERVIEW=config(
            templates=templates, context={"part": "part.xml", "title": "Hello"}
        )
    ):
        assert preview(draft)["hxml"] == f'<view xmlns="{NS}"><text>Hello</text></view>'


def test_source_order_is_preserved_for_other_names():
    raw = config(templates={"part.xml": "<text>first</text>"})
    raw["SOURCES"].append(
        {
            "BACKEND": "tests.test_engine.MemorySource",
            "OPTIONS": {"templates": {"part.xml": "<text>second</text>"}},
        }
    )
    with override_settings(HYPERVIEW=raw):
        result = preview(f'<view xmlns="{NS}">{{% include "part.xml" %}}</view>')
    assert "first" in result["hxml"] and "second" not in result["hxml"]


def test_wrapper_renders_draft_fragment_and_rejects_unrelated_root():
    templates = {
        "wrapper.xml": f'<view xmlns="{NS}">{{% include "draft.xml" %}}</view>'
    }
    with override_settings(HYPERVIEW=config(templates=templates, root="wrapper.xml")):
        assert preview("<text>Fragment</text>")["ok"] is True
    with override_settings(
        HYPERVIEW=config(templates={"wrapper.xml": VIEW}, root="wrapper.xml")
    ):
        error(preview(), "wrapper_missing_draft")


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ('{% extends "draft.xml" %}', "template_not_found"),
        ('{% include "draft.xml" %}', "template_recursion"),
        ('{% include "missing.xml" %}', "template_not_found"),
        ('{% include "../secret.xml" %}', "template_syntax"),
        ('{% include "/secret.xml" %}', "invalid_name"),
    ],
)
def test_missing_and_recursive_dependencies_are_safe(content, code):
    error(preview(content), code, "source")


def test_mutual_include_cycle_is_safe():
    with override_settings(
        HYPERVIEW=config(templates={"loop.xml": '{% include "draft.xml" %}'})
    ):
        error(preview('{% include "loop.xml" %}'), "template_recursion", "source")


def test_provider_receives_request_without_implicit_template_request_or_processors():
    request = RequestFactory().get("/private-admin-path/")
    request.user = object()
    seen = []

    def provider(current, name):
        seen.append((current, name))
        return {"title": "Explicit"}

    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "context_processors": ["django.template.context_processors.request"]
            },
        }
    ]
    content = VIEW.replace("{{ title }}", "{{ title }}|{{ request.path }}|{{ user }}")
    with override_settings(TEMPLATES=templates, HYPERVIEW=config(context=provider)):
        result = preview(content, request=request)
    assert seen == [(request, "draft.xml")]
    assert "Explicit||" in result["hxml"]
    assert "private-admin-path" not in json.dumps(result)


@pytest.mark.parametrize(
    "provider", [lambda request, name: [], lambda request, name: None]
)
def test_provider_must_return_mapping(provider):
    with override_settings(HYPERVIEW=config(context=provider)):
        error(preview(), "context_invalid")


def test_provider_exception_and_context_are_redacted():
    def provider(request, name):
        raise ValueError("/private/secret token=ABC")

    with override_settings(HYPERVIEW=config(context=provider)):
        result = preview()
    error(result, "context_error")
    assert "secret" not in json.dumps(result) and "ABC" not in json.dumps(result)


def test_syntax_error_reports_original_source_line_without_leaking_token():
    result = preview("<view>\n{% secret_invalid_tag %}\n</view>")
    diagnostic = error(result, "template_syntax", "source")
    assert diagnostic["template"] == "draft.xml"
    assert diagnostic["line"] == 2
    assert "secret_invalid_tag" not in json.dumps(result)


def test_dependency_syntax_error_reports_logical_name_not_absolute_origin():
    with override_settings(
        HYPERVIEW=config(templates={"part.xml": "\n{% secret_invalid_tag %}"})
    ):
        result = preview(f'<view xmlns="{NS}">{{% include "part.xml" %}}</view>')
    diagnostic = error(result, "template_syntax", "source")
    assert diagnostic["template"] == "part.xml" and diagnostic["line"] == 2


def test_runtime_error_reports_local_source_line_without_secret_context():
    class Broken:
        @property
        def value(self):
            raise RuntimeError("/private/SECRET")

    with override_settings(HYPERVIEW=config(context={"broken": Broken()})):
        result = preview(
            f'<view xmlns="{NS}">\n<text>{{{{ broken.value }}}}</text></view>'
        )
    diagnostic = error(result, "template_runtime", "source")
    assert diagnostic["template"] == "draft.xml" and diagnostic["line"] == 2
    assert "SECRET" not in json.dumps(result)


def test_xml_error_uses_rendered_not_original_line():
    content = "{% for value in values %}\n<text/>{% endfor %}\n<broken>"
    with override_settings(
        HYPERVIEW=config(context={"values": range(3)}, validation={"MODE": "publish"})
    ):
        diagnostic = error(preview(content), "malformed_xml", "rendered")
    assert diagnostic["line"] is not None and diagnostic["column"] is not None
    assert diagnostic["template"] == "draft.xml"


@pytest.mark.parametrize(
    ("content", "validation", "code", "space"),
    [
        (
            "<!DOCTYPE view><view/>",
            {"MODE": "publish"},
            "forbidden_declaration",
            "source",
        ),
        (VIEW, {"MAX_BYTES": 10}, "max_bytes", "source"),
        ("<view>{{ huge }}</view>", {"MAX_BYTES": 40}, "max_bytes", "rendered"),
        (
            "<view><view><view/></view></view>",
            {"MAX_DEPTH": 2},
            "max_depth",
            "rendered",
        ),
        ("<view><view/><view/></view>", {"MAX_NODES": 2}, "max_nodes", "rendered"),
        (
            '<?xml version="1.0" encoding="latin-1"?><view/>',
            {},
            "invalid_encoding",
            "source",
        ),
    ],
)
def test_source_and_render_limits_always_apply(content, validation, code, space):
    with override_settings(
        HYPERVIEW=config(context={"huge": "x" * 100}, validation=validation)
    ):
        error(preview(content), code, space)


def test_default_schema_is_enforced_even_when_normal_validation_is_publish_only():
    with override_settings(HYPERVIEW=config(validation={"MODE": "publish"})):
        error(preview("<unknown/>"), "schema", "rendered")


@pytest.mark.parametrize(
    ("profile", "accepted"), [("upstream-0.110.0", False), ("compatible-0.110.0", True)]
)
def test_default_schema_uses_selected_profile(profile, accepted):
    content = f'<styles xmlns="{NS}"><style id="x" margin="10%"/></styles>'
    with override_settings(HYPERVIEW=config(SCHEMA_PROFILE=profile)):
        assert preview(content)["ok"] is accepted


def test_configured_validator_is_honored_and_messages_redacted():
    def reject(document):
        raise TemplateValidationError("schema", "private schema /secret.xsd")

    with override_settings(HYPERVIEW=config(validation={"SCHEMA": reject})):
        result = preview()
    error(result, "schema", "rendered")
    assert "secret" not in json.dumps(result)


def test_invalid_name_scenario_disabled_and_bad_content_are_diagnostics():
    error(preview(name="../secret.xml"), "invalid_name", "source")
    error(preview(scenario="missing.secret_provider"), "invalid_scenario")
    error(preview(content=None), "invalid_content", "source")
    with override_settings(HYPERVIEW={}):
        error(preview(), "preview_disabled")


def test_empty_default_warns_without_inventing_missing_data():
    with override_settings(
        HYPERVIEW={"ADMIN": {"EDITOR": True, "PREVIEW": {"ENABLED": True}}}
    ):
        result = preview(scenario="empty")
    assert result["ok"] is True
    assert result["diagnostics"][0]["severity"] == "warning"
    assert result["diagnostics"][0]["code"] == "empty_context"
    assert "<text></text>" in result["hxml"]


def test_configuration_errors_are_not_masked_as_success():
    with override_settings(HYPERVIEW={"ADMIN": {"PREVIEW": {"ENABLED": True}}}):
        with pytest.raises(HyperviewConfigurationError):
            preview()


def test_static_nested_context_is_copied_per_preview():
    context = {"items": ["one", "two"]}
    raw = config(context=context)
    with override_settings(HYPERVIEW=raw):
        assert preview(
            VIEW.replace("{{ title }}", "{{ items.clear }}{{ items|length }}")
        )["ok"]
        assert "2" in preview(VIEW.replace("{{ title }}", "{{ items|length }}"))["hxml"]
    assert context == {"items": ["one", "two"]}


def test_extra_schema_is_used_by_default_preview_validator(tmp_path):
    path = tmp_path / "custom.xsd"
    path.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="urn:custom" xmlns:c="urn:custom" '
        'elementFormDefault="qualified"><xs:element name="card" '
        'type="xs:string"/></xs:schema>'
    )
    with override_settings(HYPERVIEW=config(EXTRA_SCHEMAS=[path])):
        assert preview('<c:card xmlns:c="urn:custom">Hello</c:card>')["ok"] is True


def test_loaded_source_safety_error_is_attributed_to_dependency():
    with override_settings(
        HYPERVIEW=config(templates={"part.xml": "<!DOCTYPE secret><view/>"})
    ):
        result = preview(f'<view xmlns="{NS}">{{% include "part.xml" %}}</view>')
    diagnostic = error(result, "forbidden_declaration", "source")
    assert diagnostic["template"] == "part.xml"


class ChangingSource(MemorySource):
    calls = 0

    def resolve(self, name):
        result = super().resolve(name)
        if name == "part.xml":
            type(self).calls += 1
            self.templates[name] = "<text>Changed</text>"
        return result


def test_preview_keeps_one_render_dependency_snapshot():
    raw = config()
    raw["SOURCES"] = [
        {
            "BACKEND": f"{__name__}.ChangingSource",
            "OPTIONS": {"templates": {"part.xml": "<text>Original</text>"}},
        }
    ]
    ChangingSource.calls = 0
    with override_settings(HYPERVIEW=raw):
        result = preview(
            f'<view xmlns="{NS}">{{% include "part.xml" %}}'
            '{% include "part.xml" %}</view>'
        )
    assert result["hxml"].count("Original") == 2
    assert ChangingSource.calls == 1


def test_consumer_exception_metadata_is_not_trusted_or_serialized():
    class Broken:
        @property
        def value(self):
            failure = RuntimeError("SECRET")
            failure.template_debug = {"name": ["SECRET"], "line": "SECRET"}
            raise failure

    with override_settings(HYPERVIEW=config(context={"broken": Broken()})):
        result = preview(VIEW.replace("{{ title }}", "{{ broken.value }}"))
    diagnostic = error(result, "template_runtime", "source")
    assert diagnostic["template"] is None and diagnostic["line"] is None
    assert "SECRET" not in json.dumps(result)


def test_custom_validator_error_code_is_not_trusted():
    def validator(document):
        raise TemplateValidationError(["SECRET"], "SECRET")

    with override_settings(HYPERVIEW=config(validation={"SCHEMA": validator})):
        result = preview()
    error(result, "schema", "rendered")
    assert "SECRET" not in json.dumps(result)


def test_named_empty_scenario_with_real_data_does_not_claim_no_examples():
    raw = config(context={"title": "Configured"})
    raw["ADMIN"]["PREVIEW"]["SCENARIOS"]["empty"] = raw["ADMIN"]["PREVIEW"][
        "SCENARIOS"
    ].pop("example")
    with override_settings(HYPERVIEW=raw):
        result = preview(scenario="empty")
    assert result["ok"] is True and result["diagnostics"] == []


class UnavailableSource:
    def __init__(self):
        raise RuntimeError("SECRET source path")


def test_source_factory_failure_is_redacted():
    raw = config()
    raw["SOURCES"] = [{"BACKEND": f"{__name__}.UnavailableSource"}]
    with override_settings(HYPERVIEW=raw):
        result = preview()
    error(result, "source_unavailable")
    assert "SECRET" not in json.dumps(result)


def test_missing_wrapper_has_no_fabricated_source_line():
    with override_settings(HYPERVIEW=config(root="missing.xml")):
        diagnostic = error(preview(), "template_not_found", "source")
    assert diagnostic["line"] is None


def test_preview_does_not_change_existing_global_engine():
    from dj_hyperview.engine import _default_engine

    current = _default_engine()
    previous = current.backend.engine.debug
    assert preview()["ok"] is True
    assert _default_engine() is current
    assert current.backend.engine.debug is previous


def test_non_dictionary_debug_metadata_cannot_break_redaction():
    class Broken:
        @property
        def value(self):
            failure = RuntimeError("SECRET")
            failure.template_debug = None
            raise failure

    with override_settings(HYPERVIEW=config(context={"broken": Broken()})):
        result = preview(VIEW.replace("{{ title }}", "{{ broken.value }}"))
    error(result, "template_runtime", "source")
    assert "SECRET" not in json.dumps(result)


def test_static_context_copy_failure_is_controlled():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("SECRET")

    with override_settings(HYPERVIEW=config(context={"value": Uncopyable()})):
        result = preview()
    error(result, "context_error")
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("content", ["<broken>", "<unknown/>"])
def test_rendered_validation_failure_retains_bounded_output_for_readonly_diagnostics(
    content,
):
    result = preview(content)
    assert result["ok"] is False
    assert result["hxml"] == content
    assert result["diagnostics"][0]["coordinate_space"] == "rendered"


@pytest.mark.parametrize("value", ["x" * 101, "é" * 60, "\ud800"])
def test_rendered_failure_omits_oversized_or_unencodable_output(value):
    with override_settings(
        HYPERVIEW=config(context={"value": value}, validation={"MAX_BYTES": 100})
    ):
        result = preview("<view>{{ value }}</view>")
    assert result["ok"] is False and result["hxml"] is None
