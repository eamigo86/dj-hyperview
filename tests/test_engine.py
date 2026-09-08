import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from django.core.exceptions import SuspiciousOperation
from django.template import TemplateDoesNotExist
from django.test import RequestFactory, override_settings
from django.test.signals import setting_changed

from dj_hyperview.engine import HyperviewEngine, render_template
from dj_hyperview.exceptions import (
    InvalidTemplateName,
    TemplateNotFound,
    TemplateValidationError,
)
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
        [
            MemorySource(
                {
                    "screen.xml": (
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " title }}</text>"
                    )
                }
            )
        ]
    )
    engine = HyperviewEngine(resolver)

    template = engine.get_template("screen.xml")

    assert len(engine.backend.engine.template_loaders) == 1
    assert isinstance(engine.backend.engine.template_loaders[0], ResolverLoader)
    assert template.origin.name == "memory:screen.xml"
    assert template.origin.template_name == "screen.xml"
    assert template.origin.loader_name == "dj_hyperview.loaders.ResolverLoader"
    assert engine.render("screen.xml", {"title": "A & B"}) == (
        '<text xmlns="https://hyperview.org/hyperview">A &amp; B</text>'
    )


def test_engine_raises_django_error_after_resolver_miss():
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(TemplateDoesNotExist) as error:
        engine.get_template("missing.xml")

    assert error.value.args[0] == "missing.xml"


def test_engine_reports_every_selected_name_after_all_miss():
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(TemplateDoesNotExist, match="first.xml, second.xml"):
        engine.render(["first.xml", "second.xml"])


def test_engine_skips_an_invalid_candidate_and_uses_the_next_template() -> None:
    """Unsafe candidates do not abort an otherwise valid ordered selection."""
    engine = HyperviewEngine(
        TemplateResolver(
            [
                MemorySource(
                    {
                        "screen.xml": (
                            '<text xmlns="https://hyperview.org/hyperview">safe</text>'
                        )
                    }
                )
            ]
        )
    )

    assert (
        engine.render(["../private.xml", "screen.xml"])
        == '<text xmlns="https://hyperview.org/hyperview">safe</text>'
    )
    assert issubclass(TemplateNotFound, TemplateDoesNotExist)
    assert issubclass(InvalidTemplateName, SuspiciousOperation)


def test_engine_rejects_an_invalid_single_name_without_disclosing_it() -> None:
    """A hostile direct lookup remains a redacted security error."""
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(InvalidTemplateName, match="^Invalid template name$") as error:
        engine.get_template("../private.xml")

    assert "private" not in str(error.value)


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "context_processors": ["django.template.context_processors.request"],
                "string_if_invalid": "INVALID",
            },
        }
    ]
)
def test_engine_inherits_consumer_django_template_options() -> None:
    """Resolver-backed rendering preserves consumer Django engine semantics."""
    engine = HyperviewEngine(
        TemplateResolver(
            [
                MemorySource(
                    {
                        "screen.xml": (
                            '<text xmlns="https://hyperview.org/hyperview">{{'
                            " request.path }}|{{ missing }}</text>"
                        )
                    }
                )
            ]
        )
    )

    assert (
        engine.render("screen.xml", request=RequestFactory().get("/configured/"))
        == '<text xmlns="https://hyperview.org/hyperview">/configured/|INVALID</text>'
    )


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"autoescape": False},
        }
    ]
)
def test_engine_does_not_inherit_consumer_autoescape_overrides() -> None:
    """Consumer engine safety overrides cannot disable HXML autoescaping."""
    engine = HyperviewEngine(
        TemplateResolver(
            [
                MemorySource(
                    {
                        "screen.xml": (
                            '<text xmlns="https://hyperview.org/hyperview">{{'
                            " value }}</text>"
                        )
                    }
                )
            ]
        )
    )

    assert engine.render("screen.xml", {"value": "<behavior />"}) == (
        '<text xmlns="https://hyperview.org/hyperview">&lt;behavior /&gt;</text>'
    )


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "tests.stubs.CustomDjangoTemplates",
            "OPTIONS": {"string_if_invalid": "SUBCLASS"},
        }
    ]
)
def test_engine_inherits_options_from_django_backend_subclasses() -> None:
    """Custom DjangoTemplates subclasses retain supported consumer options."""
    engine = HyperviewEngine(
        TemplateResolver(
            [
                MemorySource(
                    {
                        "screen.xml": (
                            '<text xmlns="https://hyperview.org/hyperview">{{'
                            " missing }}</text>"
                        )
                    }
                )
            ]
        )
    )

    assert (
        engine.render("screen.xml")
        == '<text xmlns="https://hyperview.org/hyperview">SUBCLASS</text>'
    )


def test_engine_entry_points_raise_the_package_not_found_specialization() -> None:
    """Every package engine entry point exposes one catchable miss type."""
    engine = HyperviewEngine(TemplateResolver([MemorySource({})]))

    with pytest.raises(TemplateNotFound) as captured:
        engine.get_template("missing.xml")
    assert captured.value.chain == []
    with pytest.raises(TemplateNotFound):
        engine.select_template(["first.xml", "second.xml"])


def test_extends_and_include_use_resolver_names_and_precedence():
    first = MemorySource({"parts/item.xml": "<text>first: {{ value }}</text>"})
    second = MemorySource(
        {
            "screen.xml": (
                '{% extends "layout.xml" %}'
                '{% block body %}{% include "parts/item.xml" %}{% endblock %}'
            ),
            "layout.xml": (
                '<text xmlns="https://hyperview.org/hyperview">{%'
                " block body %}{% endblock %}</text>"
            ),
            "parts/item.xml": "<text>second</text>",
        }
    )
    engine = HyperviewEngine(TemplateResolver([first, second]))

    assert engine.render("screen.xml", {"value": "A & B"}) == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        "ext>first: A &amp; B</text></text>"
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
                '<text xmlns="https://hyperview.org/hyperview">{%'
                ' include "shared.xml" %}'
                "{% include choices %}"
                '{% include "trigger.xml" %}'
                '{% include "shared.xml" %}'
                "{% include choices %}</text>"
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
        assert (
            engine.render("screen.xml", context)
            == '<text xmlns="https://hyperview.org/hyperview">newfuturenewfuture</text>'
        )
        source.resume.set()
        assert rendered.result(timeout=2) == (
            '<text xmlns="https://hyperview.org/hyperview">ol'
            "dfallbackoldfallback</text>"
        )


def test_template_response_uses_the_dedicated_engine(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "screen.xml").write_text(
        (
            '<text xmlns="https://hyperview.org/hyperview">{%'
            ' include "parts/title.xml" %}</text>'
        ),
        encoding="utf-8",
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

    assert response.content == (
        b'<text xmlns="https://hyperview.org/hyperview"><text>A &amp; B</text></text>'
    )


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [
                    (
                        "django.template.loaders.locmem.Loader",
                        {
                            "screen.xml": (
                                '<text xmlns="https://hyperview.org/hyperview">st'
                                "andard</text>"
                            )
                        },
                    )
                ]
            },
        }
    ],
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {
                    "content": '<text xmlns="https://hyperview.org/hyperview">hv</text>'
                },
            }
        ]
    },
)
def test_template_response_preserves_explicit_django_engine_selection():
    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"), "screen.xml", using="django"
    )

    assert (
        response.render().content
        == b'<text xmlns="https://hyperview.org/hyperview">standard</text>'
    )


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {
                    "content": (
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " title }}</text>"
                    )
                },
            }
        ]
    }
)
def test_render_template_uses_current_hyperview_settings():
    assert render_template("screen.xml", {"title": "Configured"}) == (
        '<text xmlns="https://hyperview.org/hyperview">Configured</text>'
    )


@override_settings(
    HYPERVIEW={
        "SOURCES": [
            {
                "BACKEND": "tests.stubs.TemplateSource",
                "OPTIONS": {
                    "content": (
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " title }}</text>"
                    )
                },
            }
        ]
    }
)
def test_default_render_paths_reuse_engine_until_hyperview_changes() -> None:
    """Convenience rendering pays engine construction once per settings snapshot."""
    setting_changed.send(sender=object, setting="HYPERVIEW", value={}, enter=True)
    with patch("dj_hyperview.engine.HyperviewEngine", wraps=HyperviewEngine) as engine:
        assert (
            render_template("screen.xml", {"title": "One"})
            == '<text xmlns="https://hyperview.org/hyperview">One</text>'
        )
        response = HyperviewTemplateResponse(
            RequestFactory().get("/screen"),
            "screen.xml",
            {"title": "Two"},
        )
        assert (
            response.render().content
            == b'<text xmlns="https://hyperview.org/hyperview">Two</text>'
        )
        setting_changed.send(sender=object, setting="OTHER", value=None, enter=True)
        assert render_template("screen.xml", {"title": "Three"}) == (
            '<text xmlns="https://hyperview.org/hyperview">Three</text>'
        )
        assert engine.call_count == 1

        setting_changed.send(sender=object, setting="TEMPLATES", value=[], enter=True)
        assert (
            render_template("screen.xml", {"title": "Four"})
            == '<text xmlns="https://hyperview.org/hyperview">Four</text>'
        )
        assert engine.call_count == 2

        setting_changed.send(sender=object, setting="HYPERVIEW", value={}, enter=True)
        assert (
            render_template("screen.xml", {"title": "Five"})
            == '<text xmlns="https://hyperview.org/hyperview">Five</text>'
        )

    assert engine.call_count == 3


def test_engine_contract_is_exported_from_package_root():
    import dj_hyperview

    assert dj_hyperview.HyperviewEngine is HyperviewEngine
    assert dj_hyperview.ResolverLoader is ResolverLoader
    assert dj_hyperview.render_template is render_template


def test_uninitialized_validated_template_can_be_copied_without_recursion() -> None:
    """Python copy protocols do not recurse through an absent wrapped template."""
    template_type = type(
        HyperviewEngine(
            TemplateResolver(
                [
                    MemorySource(
                        {
                            "screen.xml": (
                                '<text xmlns="https://hyperview.org/hyperview" />'
                            )
                        }
                    )
                ]
            )
        ).get_template("screen.xml")
    )
    uninitialized = object.__new__(template_type)

    assert isinstance(copy.copy(uninitialized), template_type)
    assert isinstance(copy.deepcopy(uninitialized), template_type)
