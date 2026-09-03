from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.template import TemplateDoesNotExist
from django.test import RequestFactory, override_settings

from dj_hyperview.engine import HyperviewEngine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.loaders import ResolverLoader
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate


class MemorySource:
    def __init__(self, templates):
        self.templates = templates

    def resolve(self, name):
        if name not in self.templates:
            return None
        content = self.templates[name]
        return ResolvedTemplate(name, content, f"memory:{name}", "memory", content)


class BlockingMutationSource(MemorySource):
    def __init__(self, templates):
        super().__init__(templates)
        self.paused = Event()
        self.resume = Event()
        self.has_paused = False

    def resolve(self, name):
        if name == "trigger.xml" and not self.has_paused:
            self.has_paused = True
            self.paused.set()
            assert self.resume.wait(timeout=2)
        return super().resolve(name)


def test_engine_renders_a_root_template_with_django_semantics():
    resolver = TemplateResolver(
        [MemorySource({"screen.xml": "<view>{{ title }}</view>"})]
    )
    engine = HyperviewEngine(resolver)

    template = engine.get_template("screen.xml")

    assert len(engine.backend.engine.template_loaders) == 1
    assert isinstance(engine.backend.engine.template_loaders[0], ResolverLoader)
    assert template.origin.name == "memory:screen.xml"
    assert template.origin.template_name == "screen.xml"
    assert template.origin.loader_name == "dj_hyperview.loaders.ResolverLoader"
    assert engine.render("screen.xml", {"title": "A & B"}) == ("<view>A &amp; B</view>")


def test_engine_raises_django_error_after_resolver_miss():
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(TemplateDoesNotExist) as error:
        engine.get_template("missing.xml")

    assert error.value.args[0] == "missing.xml"


def test_engine_reports_every_selected_name_after_all_miss():
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(TemplateDoesNotExist, match="first.xml, second.xml"):
        engine.render(["first.xml", "second.xml"])


def test_extends_and_include_use_resolver_names_and_precedence():
    first = MemorySource({"parts/item.xml": "<text>first: {{ value }}</text>"})
    second = MemorySource(
        {
            "screen.xml": (
                '{% extends "layout.xml" %}'
                '{% block body %}{% include "parts/item.xml" %}{% endblock %}'
            ),
            "layout.xml": "<view>{% block body %}{% endblock %}</view>",
            "parts/item.xml": "<text>second</text>",
        }
    )
    engine = HyperviewEngine(TemplateResolver([first, second]))

    assert engine.render("screen.xml", {"value": "A & B"}) == (
        "<view><text>first: A &amp; B</text></view>"
    )


def test_empty_root_template_is_found_then_rejected_as_invalid_hxml():
    engine = HyperviewEngine(TemplateResolver([MemorySource({"empty.xml": ""})]))

    with pytest.raises(TemplateValidationError) as error:
        engine.render("empty.xml")

    assert error.value.code == "malformed_xml"


def test_render_snapshot_does_not_mix_a_concurrent_source_mutation():
    source = BlockingMutationSource(
        {
            "screen.xml": (
                '<view>{% include "shared.xml" %}'
                "{% include choices %}"
                '{% include "trigger.xml" %}'
                '{% include "shared.xml" %}'
                "{% include choices %}</view>"
            ),
            "shared.xml": "old",
            "fallback.xml": "fallback",
            "trigger.xml": "",
        }
    )
    engine = HyperviewEngine(TemplateResolver([source]))
    context = {"choices": ["future.xml", "fallback.xml"]}

    with ThreadPoolExecutor(max_workers=1) as pool:
        rendered = pool.submit(engine.render, "screen.xml", context)
        assert source.paused.wait(timeout=2)
        source.templates["shared.xml"] = "new"
        source.templates["future.xml"] = "future"
        assert engine.render("screen.xml", context) == "<view>newfuturenewfuture</view>"
        source.resume.set()
        assert rendered.result(timeout=2) == "<view>oldfallbackoldfallback</view>"


def test_template_response_uses_the_dedicated_engine(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "screen.xml").write_text(
        '<view>{% include "parts/title.xml" %}</view>', encoding="utf-8"
    )
    (tmp_path / "parts" / "title.xml").write_text(
        "<text>{{ title }}</text>", encoding="utf-8"
    )
    configured = {
        "TEMPLATE_DIRS": [tmp_path],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    }

    with override_settings(HYPERVIEW=configured):
        response = HyperviewTemplateResponse(
            RequestFactory().get("/screen"),
            ["missing.xml", "screen.xml"],
            {"title": "A & B"},
        )
        response.render()

    assert response.content == b"<view><text>A &amp; B</text></view>"


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [
                    (
                        "django.template.loaders.locmem.Loader",
                        {"screen.xml": "<view>standard</view>"},
                    )
                ]
            },
        }
    ],
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": "<view>hv</view>"},
            }
        ]
    },
)
def test_template_response_preserves_explicit_django_engine_selection():
    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"), "screen.xml", using="django"
    )

    assert response.render().content == b"<view>standard</view>"


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {"content": "<view>{{ title }}</view>"},
            }
        ]
    }
)
def test_render_template_uses_current_hyperview_settings():
    assert render_template("screen.xml", {"title": "Configured"}) == (
        "<view>Configured</view>"
    )


def test_engine_contract_is_exported_from_package_root():
    import dj_hyperview

    assert dj_hyperview.HyperviewEngine is HyperviewEngine
    assert dj_hyperview.ResolverLoader is ResolverLoader
    assert dj_hyperview.render_template is render_template
