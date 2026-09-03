from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

from .stubs import TemplateSource


class NestedRenderValue:
    def __init__(self, render, *, after=None):
        self.render = render
        self.after = after
        self.rendered = None

    def __str__(self):
        self.rendered = self.render()
        if self.after is not None:
            self.after()
        return self.rendered


class RecoveringRenderValue:
    def __init__(self, render):
        self.render = render
        self.error_code = None

    def __str__(self):
        try:
            return self.render()
        except TemplateValidationError as error:
            self.error_code = error.code
            return "<recovered />"


class MutableSource:
    def __init__(self, templates, identity):
        self.templates = dict(templates)
        self.identity = identity

    def resolve(self, name):
        content = self.templates.get(name)
        if content is None:
            return None
        return ResolvedTemplate(
            name,
            content,
            f"{self.identity}:{name}",
            self.identity,
            content,
        )


class EqualResolver(TemplateResolver):
    def __eq__(self, other):
        return isinstance(other, EqualResolver)

    def __hash__(self):
        return 1


def test_nested_engines_isolate_same_template_name_by_resolver():
    outer = HyperviewEngine(
        TemplateResolver(
            [TemplateSource(content="<view>{{ value }}</view>", revision="outer")]
        )
    )
    inner = HyperviewEngine(
        TemplateResolver([TemplateSource(content="<inner />", revision="inner")])
    )
    value = NestedRenderValue(lambda: inner.render("screen.xml"))

    rendered = outer.render("screen.xml", {"value": value})

    assert rendered == "<view><inner /></view>"
    assert value.rendered == "<inner />"


def test_nested_engines_with_the_same_resolver_reuse_its_snapshot():
    source = MutableSource(
        {
            "screen.xml": '<view>{{ value }}{% include "shared.xml" %}</view>',
            "shared.xml": "<old />",
        },
        "shared",
    )
    resolver = TemplateResolver([source])
    outer = HyperviewEngine(resolver)
    inner = HyperviewEngine(resolver)
    value = NestedRenderValue(
        lambda: inner.render("shared.xml"),
        after=lambda: source.templates.update({"shared.xml": "<new />"}),
    )

    rendered = outer.render("screen.xml", {"value": value})

    assert value.rendered == "<old />"
    assert rendered == "<view><old /><old /></view>"
    assert outer.render("shared.xml") == "<new />"


def test_inner_scope_preserves_outer_resolutions_made_while_nested():
    outer_source = MutableSource(
        {
            "screen.xml": '<view>{{ value }}{% include "late.xml" %}</view>',
            "late.xml": "<late-old />",
        },
        "outer",
    )
    inner_source = MutableSource({"screen.xml": "<inner>{{ value }}</inner>"}, "inner")
    outer = HyperviewEngine(TemplateResolver([outer_source]))
    inner = HyperviewEngine(TemplateResolver([inner_source]))
    late_value = NestedRenderValue(lambda: outer.render("late.xml"))
    value = NestedRenderValue(
        lambda: inner.render("screen.xml", {"value": late_value}),
        after=lambda: outer_source.templates.update({"late.xml": "<late-new />"}),
    )

    rendered = outer.render("screen.xml", {"value": value})

    assert late_value.rendered == "<late-old />"
    assert rendered == "<view><inner><late-old /></inner><late-old /></view>"
    assert outer.render("late.xml") == "<late-new />"


def test_inner_exception_cleans_only_its_scope_and_next_render_sees_changes():
    outer_source = MutableSource(
        {
            "screen.xml": (
                '<view>{% include "shared.xml" %}'
                '{{ value }}{% include "shared.xml" %}</view>'
            ),
            "shared.xml": "<outer-old />",
        },
        "outer",
    )
    inner_source = MutableSource({"screen.xml": "<inner>"}, "inner")
    outer = HyperviewEngine(TemplateResolver([outer_source]))
    inner = HyperviewEngine(TemplateResolver([inner_source]))

    def render_invalid_inner():
        outer_source.templates["shared.xml"] = "<outer-new />"
        return inner.render("screen.xml")

    value = RecoveringRenderValue(render_invalid_inner)

    assert outer.render("screen.xml", {"value": value}) == (
        "<view><outer-old />&lt;recovered /&gt;<outer-old /></view>"
    )
    assert value.error_code == "malformed_xml"

    inner_source.templates["screen.xml"] = "<inner-new />"
    assert outer.render("shared.xml") == "<outer-new />"
    assert inner.render("screen.xml") == "<inner-new />"


def test_equal_resolver_objects_still_have_distinct_snapshot_identity():
    outer = HyperviewEngine(
        EqualResolver([TemplateSource(content="<view>{{ value }}</view>")])
    )
    inner = HyperviewEngine(EqualResolver([TemplateSource(content="<equal-inner />")]))
    value = NestedRenderValue(lambda: inner.render("screen.xml"))

    assert outer.render("screen.xml", {"value": value}) == (
        "<view><equal-inner /></view>"
    )
    assert value.rendered == "<equal-inner />"


def test_concurrent_reentrant_renders_do_not_cross_resolvers():
    barrier = Barrier(2)

    def render_pair(label):
        outer = HyperviewEngine(
            TemplateResolver(
                [TemplateSource(content=f"<{label}>{{{{ value }}}}</{label}>")]
            )
        )
        inner = HyperviewEngine(
            TemplateResolver([TemplateSource(content=f"<inner-{label} />")])
        )

        def render_inner():
            barrier.wait(timeout=2)
            return inner.render("screen.xml")

        return outer.render("screen.xml", {"value": NestedRenderValue(render_inner)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(render_pair, ["one", "two"]))

    assert results == [
        "<one><inner-one /></one>",
        "<two><inner-two /></two>",
    ]
