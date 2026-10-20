"""The resolver loader compiles each resolved template once while it is unchanged."""

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.template import Engine, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import Template as DjangoTemplate
from django.test import override_settings

from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, _default_engine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.loaders import ResolverLoader
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate
from dj_hyperview.validation import validate_template_source

NAMESPACE = 'xmlns="https://hyperview.org/hyperview"'


def text(body: str) -> str:
    return f"<text {NAMESPACE}>{body}</text>"


@dataclass(frozen=True, slots=True)
class TenantTemplate(ResolvedTemplate):
    """A resolved template that carries a field of its own."""

    tenant: str = ""


class Source:
    """A source whose content and identity fields the test can change at will.

    A revision of None follows the content, like a content hash. A fixed revision
    models a custom source whose revision does not follow its content.
    """

    def __init__(self, templates, *, revision=None, identity="memory", prefix="memory"):
        self.templates = dict(templates)
        self.revision = revision
        self.identity = identity
        self.prefix = prefix
        self.tenant = None
        self.resolves = 0

    def resolve(self, name):
        self.resolves += 1
        content = self.templates.get(name)
        if content is None:
            return None
        fields = (
            name,
            content,
            f"{self.prefix}:{name}",
            self.identity,
            content if self.revision is None else self.revision,
        )
        if self.tenant is None:
            return ResolvedTemplate(*fields)
        return TenantTemplate(*fields, tenant=self.tenant)


def engine_for(source, **options):
    return HyperviewEngine(TemplateResolver([source]), **options)


def loader_for(source, validation=None):
    return ResolverLoader(
        Engine(), TemplateResolver([source]), validation or ValidationSettings()
    )


def assert_accounted(loader, entries=None):
    """The byte count must match the stored contents, whatever happened before."""
    stored = [resolved.content for resolved, _ in loader._compiled.values()]
    assert loader._compiled_size == sum(map(len, stored))
    if entries is not None:
        assert len(stored) == entries


@pytest.fixture
def counts():
    """Count the Django compiles and the loader's source validations."""
    original = DjangoTemplate.compile_nodelist
    with (
        patch.object(
            DjangoTemplate, "compile_nodelist", autospec=True, side_effect=original
        ) as compiled,
        patch(
            "dj_hyperview.loaders.validate_template_source",
            wraps=validate_template_source,
        ) as validated,
    ):
        yield SimpleNamespace(compiled=compiled, validated=validated)


def test_a_repeated_render_validates_and_compiles_once(counts) -> None:
    source = Source({"screen.xml": text("{{ v }}")})
    engine = engine_for(source)

    outputs = [engine.render("screen.xml", {"v": n}) for n in range(3)]

    assert outputs == [text("0"), text("1"), text("2")]
    assert counts.validated.call_count == 1
    assert counts.compiled.call_count == 1
    # The resolver still runs on every render, so invalidation behaves as before.
    assert source.resolves == 3


def test_a_hit_returns_the_compiled_template_of_the_first_resolution() -> None:
    loader = loader_for(Source({"screen.xml": text("one")}))

    first = loader.get_template("screen.xml")

    assert loader.get_template("screen.xml") is first
    assert first.origin.resolved.content == text("one")


def test_a_new_revision_is_compiled_again(counts) -> None:
    source = Source({"screen.xml": text("one")}, revision="r1")
    engine = engine_for(source)
    assert engine.render("screen.xml") == text("one")
    assert engine.render("screen.xml") == text("one")
    assert counts.compiled.call_count == 1

    source.revision = "r2"
    assert engine.render("screen.xml") == text("one")
    assert counts.compiled.call_count == 2

    source.templates["screen.xml"] = text("two")
    source.revision = "r3"
    assert engine.render("screen.xml") == text("two")
    assert counts.compiled.call_count == 3
    assert counts.validated.call_count == 3


def test_republishing_a_template_keeps_one_entry_and_the_live_byte_count() -> None:
    """A superseded revision must not stay in memory until the LRU evicts it."""
    source = Source({"screen.xml": text("")})
    loader = loader_for(source)

    for n in range(1, 21):  # every revision is longer than the one it replaces
        source.templates["screen.xml"] = text("x" * n)
        loader.get_template("screen.xml")

    assert_accounted(loader, entries=1)
    assert loader._compiled_size == len(text("x" * 20))


def test_a_shrinking_republish_leaves_the_smaller_byte_count() -> None:
    source = Source({"screen.xml": text("x" * 50)})
    loader = loader_for(source)
    loader.get_template("screen.xml")

    source.templates["screen.xml"] = text("x")
    loader.get_template("screen.xml")

    assert_accounted(loader, entries=1)
    assert loader._compiled_size == len(text("x"))


def test_a_republish_that_outgrows_the_budget_drops_the_old_entry(monkeypatch) -> None:
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", 3 * len(text("a")))
    source = Source({"screen.xml": text("a")})
    loader = loader_for(source)
    loader.get_template("screen.xml")

    source.templates["screen.xml"] = text("x" * 1000)
    loader.get_template("screen.xml")

    assert_accounted(loader, entries=0)


def test_changed_content_under_the_same_revision_is_never_served_stale(
    counts,
) -> None:
    """A custom source need not change its revision with its content."""
    source = Source({"screen.xml": text("one")}, revision="fixed")
    engine = engine_for(source)
    assert engine.render("screen.xml") == text("one")

    source.templates["screen.xml"] = text("two")
    assert engine.render("screen.xml") == text("two")
    assert engine.render("screen.xml") == text("two")
    assert counts.compiled.call_count == 2

    source.templates["screen.xml"] = text("one")
    assert engine.render("screen.xml") == text("one")
    assert counts.compiled.call_count == 3


@pytest.mark.parametrize("field", ["identity", "prefix"])
def test_another_source_or_origin_is_a_different_entry(counts, field) -> None:
    source = Source({"screen.xml": text("one")}, identity="first", prefix="first")
    engine = engine_for(source)
    loader = engine.backend.engine.template_loaders[0]
    assert engine.get_template("screen.xml").origin.name == "first:screen.xml"

    setattr(source, field, "second")
    template = engine.get_template("screen.xml")

    assert counts.compiled.call_count == 2
    assert template.origin.name == f"{source.prefix}:screen.xml"
    assert template.origin.resolved.source == source.identity
    for value in ("first", "second"):  # each keeps its entry, so switching is a hit
        setattr(source, field, value)
        engine.get_template("screen.xml")
    assert counts.compiled.call_count == 2
    assert_accounted(loader, entries=2)


def test_a_field_outside_the_key_that_differs_is_a_miss_with_the_fresh_origin(
    counts,
) -> None:
    """A subclass may carry fields of its own, so the whole resolution must match."""
    source = Source({"screen.xml": text("one")})
    source.tenant = "a"
    engine = engine_for(source)
    assert engine.get_template("screen.xml").origin.resolved.tenant == "a"
    assert engine.get_template("screen.xml").origin.resolved.tenant == "a"
    assert counts.compiled.call_count == 1

    source.tenant = "b"
    assert engine.get_template("screen.xml").origin.resolved.tenant == "b"
    assert counts.compiled.call_count == 2


def test_the_entry_count_is_bounded_and_the_oldest_use_leaves_first(
    counts, monkeypatch
) -> None:
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_ENTRIES", 2)
    engine = engine_for(Source({f"{n}.xml": text(n) for n in "abc"}))

    for name in ("a", "b", "a", "c"):  # c evicts b, the least recently used
        engine.render(f"{name}.xml")
    assert counts.compiled.call_count == 3

    engine.render("a.xml")  # still cached
    assert counts.compiled.call_count == 3
    engine.render("b.xml")  # evicted, so compiled again
    assert counts.compiled.call_count == 4


def test_the_total_source_size_is_bounded(counts, monkeypatch) -> None:
    size = len(text("a"))
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", 2 * size)
    engine = engine_for(Source({f"{n}.xml": text(n) for n in "abc"}))

    for name in ("a", "b", "a", "c"):
        engine.render(f"{name}.xml")
    assert counts.compiled.call_count == 3
    engine.render("a.xml")
    assert counts.compiled.call_count == 3
    engine.render("b.xml")
    assert counts.compiled.call_count == 4


def test_a_source_larger_than_the_whole_budget_is_never_cached_and_evicts_nothing(
    counts, monkeypatch
) -> None:
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", 2 * len(text("a")))
    engine = engine_for(
        Source({"a.xml": text("a"), "b.xml": text("b"), "big.xml": text("x" * 1000)})
    )
    engine.render("a.xml")
    engine.render("b.xml")

    engine.render("big.xml")
    engine.render("big.xml")
    assert counts.compiled.call_count == 4  # big is compiled on every render

    engine.render("a.xml")  # and it did not push out what fits
    engine.render("b.xml")
    assert counts.compiled.call_count == 4


def test_a_stricter_validation_policy_is_enforced_on_the_next_render() -> None:
    loader = loader_for(Source({"screen.xml": text("one")}))
    loader.get_template("screen.xml")

    loader.validation = ValidationSettings(max_bytes=10)

    with pytest.raises(TemplateValidationError) as error:
        loader.get_template("screen.xml")
    assert error.value.code == "max_bytes"


STUB_SOURCES = [
    {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"content": text("hv")}}
]


def test_lowering_a_validation_limit_is_enforced_on_the_next_render() -> None:
    """Changing HYPERVIEW rebuilds the default engine, and its cache with it."""
    with override_settings(HYPERVIEW={"SOURCES": STUB_SOURCES}):
        assert render_template("screen.xml") == text("hv")
        assert render_template("screen.xml") == text("hv")
        with override_settings(
            HYPERVIEW={"SOURCES": STUB_SOURCES, "VALIDATION": {"MAX_BYTES": 10}}
        ):
            with pytest.raises(TemplateValidationError) as error:
                render_template("screen.xml")
            assert error.value.code == "max_bytes"
        assert render_template("screen.xml") == text("hv")


def test_a_templates_change_rebuilds_the_default_engine_and_its_cache() -> None:
    """HYPERVIEW and DEBUG changes are covered by the tests above and test_engine."""
    with override_settings(HYPERVIEW={"SOURCES": STUB_SOURCES}):
        before = _default_engine().backend.engine.template_loaders[0]
        before.get_template("screen.xml")

        with override_settings(TEMPLATES=[]):
            after = _default_engine().backend.engine.template_loaders[0]

        assert after is not before
        assert after.get_template("screen.xml") is not before.get_template("screen.xml")


def test_extends_and_include_chains_compile_each_template_once(counts) -> None:
    source = Source(
        {
            "base.xml": text(
                '{% block body %}base{% endblock %}{% include "part.xml" %}'
            ),
            "child.xml": '{% extends "base.xml" %}{% block body %}child{% endblock %}',
            "part.xml": "-{{ v }}",
        }
    )
    engine = engine_for(source)

    for n in range(3):
        assert engine.render("child.xml", {"v": n}) == text(f"child-{n}")
    assert counts.compiled.call_count == 3  # child, base and part, once each

    source.templates["base.xml"] = text("[{% block body %}{% endblock %}]")
    assert engine.render("child.xml") == text("[child]")
    assert counts.compiled.call_count == 4  # only the changed parent


def test_extends_recursion_is_still_refused_with_a_warm_cache() -> None:
    loader = loader_for(Source({"screen.xml": text("one")}))
    template = loader.get_template("screen.xml")

    with pytest.raises(TemplateDoesNotExist):
        loader.get_template("screen.xml", skip=[template.origin])

    assert loader.get_template("screen.xml") is template


@pytest.mark.parametrize("warm", [False, True])
def test_a_template_cannot_extend_itself_or_its_own_ancestor(warm) -> None:
    engine = engine_for(
        Source(
            {
                "self.xml": '{% extends "self.xml" %}',
                "a.xml": '{% extends "b.xml" %}',
                "b.xml": '{% extends "a.xml" %}',
            }
        )
    )
    for name in ("self.xml", "a.xml"):
        for _ in range(2 if warm else 1):
            with pytest.raises(TemplateDoesNotExist):
                engine.render(name)


def test_compile_errors_are_not_cached(counts) -> None:
    source = Source({"screen.xml": "{% bogus %}"}, revision="fixed")
    engine = engine_for(source)

    for _ in range(2):
        with pytest.raises(TemplateSyntaxError):
            engine.render("screen.xml")
    assert counts.compiled.call_count == 2

    source.templates["screen.xml"] = text("fixed")
    assert engine.render("screen.xml") == text("fixed")
    assert engine.render("screen.xml") == text("fixed")
    assert counts.compiled.call_count == 3


def test_validation_errors_are_not_cached(counts) -> None:
    engine = engine_for(
        Source({"screen.xml": text("one")}), validation=ValidationSettings(max_bytes=10)
    )

    for _ in range(2):
        with pytest.raises(TemplateValidationError):
            engine.render("screen.xml")

    assert counts.validated.call_count == 2
    assert counts.compiled.call_count == 0


@pytest.mark.parametrize("step", ["get_contents", "get_template_sources"])
def test_a_loader_that_overrides_a_step_compiles_every_time(counts, step) -> None:
    """An override may read ambient state, so its result is not shared."""

    class Subclass(ResolverLoader):
        pass

    def override(self, *args):
        return getattr(super(Subclass, self), step)(*args)

    setattr(Subclass, step, override)
    source = Source({"screen.xml": text("one")})
    subclass = Subclass(Engine(), TemplateResolver([source]), ValidationSettings())
    patched = loader_for(source)

    assert subclass.get_template("screen.xml") is not subclass.get_template(
        "screen.xml"
    )
    with patch.object(patched, step, wraps=getattr(patched, step)):
        assert patched.get_template("screen.xml") is not patched.get_template(
            "screen.xml"
        )
    assert counts.compiled.call_count == 4
    assert_accounted(subclass, entries=0)
    assert_accounted(patched, entries=0)


def test_reset_forgets_the_compiled_templates() -> None:
    loader = loader_for(Source({"screen.xml": text("one")}))
    first = loader.get_template("screen.xml")

    loader.reset()

    assert loader.get_template("screen.xml") is not first


def test_two_engines_do_not_share_compiled_templates() -> None:
    source = Source({"screen.xml": text("one")})

    assert loader_for(source).get_template("screen.xml") is not loader_for(
        source
    ).get_template("screen.xml")


def test_concurrent_renders_share_compiled_templates_safely(counts) -> None:
    names = [f"screen{n}.xml" for n in range(3)]
    source = Source(
        {
            name: text(f"{n}:{{% load dj_hyperview %}}{{{{ v|hv_text }}}}")
            for n, name in enumerate(names)
        }
    )
    engine = engine_for(source)
    threads = 8
    barrier = Barrier(threads)

    def work(thread):
        barrier.wait(timeout=10)
        return [
            (engine.render(names[i % 3], {"v": f"<{thread}-{i}>"}), thread, i)
            for i in range(40)
        ]

    with ThreadPoolExecutor(threads) as pool:
        results = [row for rows in pool.map(work, range(threads)) for row in rows]

    for rendered, thread, i in results:
        assert rendered == text(f"{i % 3}:&lt;{thread}-{i}&gt;")
    assert 3 <= counts.compiled.call_count <= 3 * threads


@pytest.fixture
def fast_thread_switching():
    """Make the interpreter switch threads often, so an unguarded race shows up."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def test_threads_that_hit_evict_and_replace_keep_the_accounting_exact(
    fast_thread_switching, monkeypatch
) -> None:
    budget = 3 * len(text("")) + 150
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_ENTRIES", 3)
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", budget)
    # Sizes differ, so both the entry cap and the character budget evict.
    names = [f"screen{n}.xml" for n in range(8)]
    source = Source({name: text("x" * 10 * (n + 1)) for n, name in enumerate(names)})
    loader = loader_for(source)
    threads = 8
    barrier = Barrier(threads)

    def work(thread):
        barrier.wait(timeout=10)
        for i in range(150):
            n = (thread * 3 + i * 5) % len(names)
            if i % 25 == 0:  # republish, so a hit races a replacement
                source.templates[names[n]] = text("x" * (10 * (n + 1) + i % 7))
            loader.get_template(names[n])

    with ThreadPoolExecutor(threads) as pool:
        for future in [pool.submit(work, thread) for thread in range(threads)]:
            future.result(timeout=60)  # raises what the thread raised

    assert len(loader._compiled) <= 3
    assert loader._compiled_size <= budget
    assert_accounted(loader)
