"""Nested renders keep the resolver snapshots of their own engines apart."""

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

from .stubs import TemplateSource


class NestedRenderValue:
    """A value that renders another template while its own string form is built.

    Args:
        render: Callable that renders the inner template.
        after: Callable that runs after the inner render, or None.
    """

    def __init__(
        self, render: Callable[[], str], *, after: Callable[[], object] | None = None
    ) -> None:
        """Store the render callable and the callable that runs after it."""
        self.render = render
        self.after = after
        self.rendered = None

    def __str__(self) -> str:
        """Render the inner template, run the follow-up and return the markup."""
        self.rendered = self.render()
        if self.after is not None:
            self.after()
        return self.rendered


class RecoveringRenderValue:
    """A value that recovers from a validation error of its inner render.

    Args:
        render: Callable that renders the inner template.
    """

    def __init__(self, render: Callable[[], str]) -> None:
        """Store the render callable."""
        self.render = render
        self.error_code = None

    def __str__(self) -> str:
        """Render the inner template, or return a marker after a validation error."""
        try:
            return self.render()
        except TemplateValidationError as error:
            self.error_code = error.code
            return "<recovered />"


class MutableSource:
    """A source whose templates the test can change between renders.

    Args:
        templates: Template source by template name.
        identity: Identity the source reports for its templates.
    """

    def __init__(self, templates: Mapping[str, str], identity: str) -> None:
        """Store a copy of the templates and the identity."""
        self.templates = dict(templates)
        self.identity = identity

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve a template by name from the dictionary.

        Args:
            name: Name of the template to resolve.

        Returns:
            The resolved template, or None when the name is unknown.
        """
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
    """A resolver that equals every other instance and hashes to the same value."""

    def __eq__(self, other: object) -> bool:
        """Equal every other instance of this class."""
        return isinstance(other, EqualResolver)

    def __hash__(self) -> int:
        """Hash every instance to the same value."""
        return 1


def test_nested_engines_isolate_same_template_name_by_resolver() -> None:
    """Nested engines isolate same template name by resolver."""
    outer = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " value }}</text>"
                    ),
                    revision="outer",
                )
            ]
        )
    )
    inner = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        '<text xmlns="https://hyperview.org/hyperview" id="inner" />'
                    ),
                    revision="inner",
                )
            ]
        )
    )
    value = NestedRenderValue(lambda: inner.render("screen.xml"))

    rendered = outer.render("screen.xml", {"value": value})

    assert rendered == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        'ext xmlns="https://hyperview.org/hyperview" id="'
        'inner" /></text>'
    )
    assert (
        value.rendered == '<text xmlns="https://hyperview.org/hyperview" id="inner" />'
    )


def test_nested_engines_with_the_same_resolver_reuse_its_snapshot() -> None:
    """Nested engines with the same resolver reuse its snapshot."""
    source = MutableSource(
        {
            "screen.xml": (
                '<text xmlns="https://hyperview.org/hyperview">{{'
                ' value }}{% include "shared.xml" %}</text>'
            ),
            "shared.xml": '<text xmlns="https://hyperview.org/hyperview" id="old" />',
        },
        "shared",
    )
    resolver = TemplateResolver([source])
    outer = HyperviewEngine(resolver)
    inner = HyperviewEngine(resolver)
    value = NestedRenderValue(
        lambda: inner.render("shared.xml"),
        after=lambda: source.templates.update(
            {"shared.xml": '<text xmlns="https://hyperview.org/hyperview" id="new" />'}
        ),
    )

    rendered = outer.render("screen.xml", {"value": value})

    assert value.rendered == '<text xmlns="https://hyperview.org/hyperview" id="old" />'
    assert rendered == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        'ext xmlns="https://hyperview.org/hyperview" id="'
        'old" /><text xmlns="https://hyperview.org/hyperv'
        'iew" id="old" /></text>'
    )
    assert (
        outer.render("shared.xml")
        == '<text xmlns="https://hyperview.org/hyperview" id="new" />'
    )


def test_inner_scope_preserves_outer_resolutions_made_while_nested() -> None:
    """Inner scope preserves outer resolutions made while nested."""
    outer_source = MutableSource(
        {
            "screen.xml": (
                '<text xmlns="https://hyperview.org/hyperview">{{'
                ' value }}{% include "late.xml" %}</text>'
            ),
            "late.xml": (
                '<text xmlns="https://hyperview.org/hyperview" id="late-old" />'
            ),
        },
        "outer",
    )
    inner_source = MutableSource(
        {
            "screen.xml": (
                '<text xmlns="https://hyperview.org/hyperview" id'
                '="inner">{{ value }}</text>'
            )
        },
        "inner",
    )
    outer = HyperviewEngine(TemplateResolver([outer_source]))
    inner = HyperviewEngine(TemplateResolver([inner_source]))
    late_value = NestedRenderValue(lambda: outer.render("late.xml"))
    value = NestedRenderValue(
        lambda: inner.render("screen.xml", {"value": late_value}),
        after=lambda: outer_source.templates.update(
            {
                "late.xml": (
                    '<text xmlns="https://hyperview.org/hyperview" id="late-new" />'
                )
            }
        ),
    )

    rendered = outer.render("screen.xml", {"value": value})

    assert (
        late_value.rendered
        == '<text xmlns="https://hyperview.org/hyperview" id="late-old" />'
    )
    assert rendered == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        'ext xmlns="https://hyperview.org/hyperview" id="'
        'inner"><text xmlns="https://hyperview.org/hyperv'
        'iew" id="late-old" /></text><text xmlns="https:/'
        '/hyperview.org/hyperview" id="late-old" /></text'
        ">"
    )
    assert (
        outer.render("late.xml")
        == '<text xmlns="https://hyperview.org/hyperview" id="late-new" />'
    )


def test_inner_exception_cleans_only_its_scope_and_next_render_sees_changes() -> None:
    """Inner exception cleans only its scope and next render sees changes."""
    outer_source = MutableSource(
        {
            "screen.xml": (
                '<text xmlns="https://hyperview.org/hyperview">{%'
                ' include "shared.xml" %}'
                '{{ value }}{% include "shared.xml" %}</text>'
            ),
            "shared.xml": (
                '<text xmlns="https://hyperview.org/hyperview" id="outer-old" />'
            ),
        },
        "outer",
    )
    inner_source = MutableSource(
        {"screen.xml": '<text xmlns="https://hyperview.org/hyperview" id="inner">'},
        "inner",
    )
    outer = HyperviewEngine(TemplateResolver([outer_source]))
    inner = HyperviewEngine(TemplateResolver([inner_source]))

    def render_invalid_inner() -> str:
        outer_source.templates["shared.xml"] = (
            '<text xmlns="https://hyperview.org/hyperview" id="outer-new" />'
        )
        return inner.render("screen.xml")

    value = RecoveringRenderValue(render_invalid_inner)

    assert outer.render("screen.xml", {"value": value}) == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        'ext xmlns="https://hyperview.org/hyperview" id="'
        'outer-old" />&lt;recovered /&gt;<text xmlns="htt'
        'ps://hyperview.org/hyperview" id="outer-old" /><'
        "/text>"
    )
    assert value.error_code == "malformed_xml"

    inner_source.templates["screen.xml"] = (
        '<text xmlns="https://hyperview.org/hyperview" id="inner-new" />'
    )
    assert (
        outer.render("shared.xml")
        == '<text xmlns="https://hyperview.org/hyperview" id="outer-new" />'
    )
    assert (
        inner.render("screen.xml")
        == '<text xmlns="https://hyperview.org/hyperview" id="inner-new" />'
    )


def test_equal_resolver_objects_still_have_distinct_snapshot_identity() -> None:
    """Equal resolver objects still have distinct snapshot identity."""
    outer = HyperviewEngine(
        EqualResolver(
            [
                TemplateSource(
                    content=(
                        '<text xmlns="https://hyperview.org/hyperview">{{'
                        " value }}</text>"
                    )
                )
            ]
        )
    )
    inner = HyperviewEngine(
        EqualResolver(
            [
                TemplateSource(
                    content=(
                        '<text xmlns="https://hyperview.org/hyperview" id'
                        '="equal-inner" />'
                    )
                )
            ]
        )
    )
    value = NestedRenderValue(lambda: inner.render("screen.xml"))

    assert outer.render("screen.xml", {"value": value}) == (
        '<text xmlns="https://hyperview.org/hyperview"><t'
        'ext xmlns="https://hyperview.org/hyperview" id="'
        'equal-inner" /></text>'
    )
    assert (
        value.rendered
        == '<text xmlns="https://hyperview.org/hyperview" id="equal-inner" />'
    )


def test_concurrent_reentrant_renders_do_not_cross_resolvers() -> None:
    """Concurrent reentrant renders do not cross resolvers."""
    barrier = Barrier(2)

    def render_pair(label: str) -> str:
        outer = HyperviewEngine(
            TemplateResolver(
                [
                    TemplateSource(
                        content=(
                            '<text xmlns="https://hyperview.org/hyperview" '
                            f'id="{label}">{{{{ value }}}}</text>'
                        )
                    )
                ]
            )
        )
        inner = HyperviewEngine(
            TemplateResolver(
                [
                    TemplateSource(
                        content=(
                            '<text xmlns="https://hyperview.org/hyperview" '
                            f'id="inner-{label}" />'
                        )
                    )
                ]
            )
        )

        def render_inner() -> str:
            barrier.wait(timeout=2)
            return inner.render("screen.xml")

        return outer.render("screen.xml", {"value": NestedRenderValue(render_inner)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(render_pair, ["one", "two"]))

    assert results == [
        (
            '<text xmlns="https://hyperview.org/hyperview" id'
            '="one"><text xmlns="https://hyperview.org/hyperv'
            'iew" id="inner-one" /></text>'
        ),
        (
            '<text xmlns="https://hyperview.org/hyperview" id'
            '="two"><text xmlns="https://hyperview.org/hyperv'
            'iew" id="inner-two" /></text>'
        ),
    ]
