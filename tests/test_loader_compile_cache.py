"""The resolver loader compiles each resolved template once while it is unchanged."""

import copy
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from django.template import Context, Engine, TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import Origin
from django.template.base import Template as DjangoTemplate
from django.test import TestCase, override_settings

from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine, _default_engine, render_template
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.loaders import ResolverLoader, _CompiledCache
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate
from dj_hyperview.validation import validate_template_source

NAMESPACE = 'xmlns="https://hyperview.org/hyperview"'


def text(body: str) -> str:
    """Wrap a text body in an element of the Hyperview namespace.

    Args:
        body: Text content of the element.

    Returns:
        The markup of the element.
    """
    return f"<text {NAMESPACE}>{body}</text>"


@dataclass(frozen=True, slots=True)
class TenantTemplate(ResolvedTemplate):
    """A resolved template that carries a field of its own."""

    tenant: str = ""


class Source:
    """A source whose content and identity fields the test can change at will.

    A revision of None follows the content, like a content hash. A fixed revision
    models a custom source whose revision does not follow its content.

    Args:
        templates: Template source by template name.
        revision: Fixed revision of the source, or None to follow the content.
        identity: Identity the source reports for the templates it resolves.
        prefix: Prefix of the origin name of each template.
    """

    def __init__(
        self,
        templates: dict[str, str],
        *,
        revision: str | None = None,
        identity: str = "memory",
        prefix: str = "memory",
    ) -> None:
        """Store the templates and the fields the test can change."""
        self.templates = dict(templates)
        self.revision = revision
        self.identity = identity
        self.prefix = prefix
        self.tenant = None
        self.resolves = 0

    def resolve(self, name: str) -> ResolvedTemplate | None:
        """Resolve a template by name and count the call.

        Args:
            name: Name of the template to resolve.

        Returns:
            The resolved template, or None when the name is unknown.
        """
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


def engine_for(source: Source, **options: Any) -> HyperviewEngine:
    """Build an engine that resolves templates from one source.

    Args:
        source: Source that the loader or engine reads.
        **options: Options passed to the engine.

    Returns:
        An engine that resolves templates from the given source.
    """
    return HyperviewEngine(TemplateResolver([source]), **options)


def loader_for(
    source: Source, validation: ValidationSettings | None = None
) -> ResolverLoader:
    """Build a loader that resolves templates from one source.

    Args:
        source: Source that the loader or engine reads.
        validation: Validation settings of the loader, or None for the defaults.

    Returns:
        A loader that resolves templates from the given source.
    """
    return ResolverLoader(
        Engine(), TemplateResolver([source]), validation or ValidationSettings()
    )


def assert_accounted(loader: ResolverLoader, entries: int | None = None) -> None:
    """The byte count must match the stored contents, whatever happened before.

    Args:
        loader: Loader whose cache is checked.
        entries: Expected number of cached entries, or None to skip that check.
    """
    stored = [resolved.content for resolved, _ in loader._compiled.entries.values()]
    assert loader._compiled.size == sum(map(len, stored))
    if entries is not None:
        assert len(stored) == entries


@pytest.fixture
def counts() -> Iterator[SimpleNamespace]:
    """Count the Django compiles and the loader's source validations.

    Yields:
        The compile and validation counters.
    """
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


def test_a_repeated_render_validates_and_compiles_once(counts: SimpleNamespace) -> None:
    """A repeated render validates and compiles once.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
    source = Source({"screen.xml": text("{{ v }}")})
    engine = engine_for(source)

    outputs = [engine.render("screen.xml", {"v": n}) for n in range(3)]

    assert outputs == [text("0"), text("1"), text("2")]
    assert counts.validated.call_count == 1
    assert counts.compiled.call_count == 1
    # The resolver still runs on every render, so invalidation behaves as before.
    assert source.resolves == 3


def test_a_hit_returns_the_compiled_template_of_the_first_resolution() -> None:
    """A hit returns the compiled template of the first resolution."""
    loader = loader_for(Source({"screen.xml": text("one")}))

    first = loader.get_template("screen.xml")

    assert loader.get_template("screen.xml") is first
    assert first.origin.resolved.content == text("one")


def test_a_new_revision_is_compiled_again(counts: SimpleNamespace) -> None:
    """A new revision is compiled again.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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
    assert loader._compiled.size == len(text("x" * 20))


def test_a_shrinking_republish_leaves_the_smaller_byte_count() -> None:
    """A shrinking republish leaves the smaller byte count."""
    source = Source({"screen.xml": text("x" * 50)})
    loader = loader_for(source)
    loader.get_template("screen.xml")

    source.templates["screen.xml"] = text("x")
    loader.get_template("screen.xml")

    assert_accounted(loader, entries=1)
    assert loader._compiled.size == len(text("x"))


def test_a_republish_that_outgrows_the_budget_drops_the_old_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A republish that outgrows the budget drops the old entry.

    Args:
        monkeypatch: Patches the code under test for this test.
    """
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", 3 * len(text("a")))
    source = Source({"screen.xml": text("a")})
    loader = loader_for(source)
    loader.get_template("screen.xml")

    source.templates["screen.xml"] = text("x" * 1000)
    loader.get_template("screen.xml")

    assert_accounted(loader, entries=0)


def test_changed_content_under_the_same_revision_is_never_served_stale(
    counts: SimpleNamespace,
) -> None:
    """A custom source need not change its revision with its content.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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
def test_another_source_or_origin_is_a_different_entry(
    counts: SimpleNamespace, field: str
) -> None:
    """Another source or origin is a different entry.

    Args:
        counts: Counters of the Django compiles and of the source validations.
        field: Name of the source attribute that the test changes.
    """
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
    counts: SimpleNamespace,
) -> None:
    """A subclass may carry fields of its own, so the whole resolution must match.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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
    counts: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry count is bounded and the oldest use leaves first.

    Args:
        counts: Counters of the Django compiles and of the source validations.
        monkeypatch: Patches the code under test for this test.
    """
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_ENTRIES", 2)
    engine = engine_for(Source({f"{n}.xml": text(n) for n in "abc"}))

    for name in ("a", "b", "a", "c"):  # c evicts b, the least recently used
        engine.render(f"{name}.xml")
    assert counts.compiled.call_count == 3

    engine.render("a.xml")  # still cached
    assert counts.compiled.call_count == 3
    engine.render("b.xml")  # evicted, so compiled again
    assert counts.compiled.call_count == 4


def test_the_total_source_size_is_bounded(
    counts: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The total source size is bounded.

    Args:
        counts: Counters of the Django compiles and of the source validations.
        monkeypatch: Patches the code under test for this test.
    """
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
    counts: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source larger than the whole budget is never cached and evicts nothing.

    Args:
        counts: Counters of the Django compiles and of the source validations.
        monkeypatch: Patches the code under test for this test.
    """
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
    """A stricter validation policy is enforced on the next render."""
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


def test_extends_and_include_chains_compile_each_template_once(
    counts: SimpleNamespace,
) -> None:
    """Extends and include chains compile each template once.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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
    """Extends recursion is still refused with a warm cache."""
    loader = loader_for(Source({"screen.xml": text("one")}))
    template = loader.get_template("screen.xml")

    with pytest.raises(TemplateDoesNotExist):
        loader.get_template("screen.xml", skip=[template.origin])

    assert loader.get_template("screen.xml") is template


@pytest.mark.parametrize("warm", [False, True])
def test_a_template_cannot_extend_itself_or_its_own_ancestor(warm: bool) -> None:
    """A template cannot extend itself or its own ancestor.

    Args:
        warm: Whether the template is loaded once before the test.
    """
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


def test_compile_errors_are_not_cached(counts: SimpleNamespace) -> None:
    """Compile errors are not cached.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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


def test_validation_errors_are_not_cached(counts: SimpleNamespace) -> None:
    """Validation errors are not cached.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
    engine = engine_for(
        Source({"screen.xml": text("one")}), validation=ValidationSettings(max_bytes=10)
    )

    for _ in range(2):
        with pytest.raises(TemplateValidationError):
            engine.render("screen.xml")

    assert counts.validated.call_count == 2
    assert counts.compiled.call_count == 0


@pytest.mark.parametrize("step", ["get_contents", "get_template_sources"])
def test_a_loader_that_overrides_a_step_compiles_every_time(
    counts: SimpleNamespace, step: str
) -> None:
    """An override may read ambient state, so its result is not shared.

    Args:
        counts: Counters of the Django compiles and of the source validations.
        step: Name of the loader step that the subclass overrides.
    """

    class Subclass(ResolverLoader):
        pass

    def override(self: ResolverLoader, *args: Any) -> Any:
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
    """Reset forgets the compiled templates."""
    loader = loader_for(Source({"screen.xml": text("one")}))
    first = loader.get_template("screen.xml")

    loader.reset()

    assert loader.get_template("screen.xml") is not first


def test_two_engines_do_not_share_compiled_templates() -> None:
    """Two engines do not share compiled templates."""
    source = Source({"screen.xml": text("one")})

    assert loader_for(source).get_template("screen.xml") is not loader_for(
        source
    ).get_template("screen.xml")


def test_concurrent_renders_share_compiled_templates_safely(
    counts: SimpleNamespace,
) -> None:
    """Concurrent renders share compiled templates safely.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
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

    def work(thread: int) -> list[tuple[str, int, int]]:
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
def fast_thread_switching() -> Iterator[None]:
    """Make the interpreter switch threads often, so an unguarded race shows up.

    Yields:
        Nothing, while the thread switch interval is shortened.
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def test_threads_that_hit_evict_and_replace_keep_the_accounting_exact(
    fast_thread_switching: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Threads that hit evict and replace keep the accounting exact.

    Args:
        fast_thread_switching: Makes the interpreter switch threads often for this test.
        monkeypatch: Patches the code under test for this test.
    """
    budget = 3 * len(text("")) + 150
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_ENTRIES", 3)
    monkeypatch.setattr(ResolverLoader, "_COMPILED_MAX_CHARS", budget)
    # Sizes differ, so both the entry cap and the character budget evict.
    names = [f"screen{n}.xml" for n in range(8)]
    source = Source({name: text("x" * 10 * (n + 1)) for n, name in enumerate(names)})
    loader = loader_for(source)
    threads = 8
    barrier = Barrier(threads)

    def work(thread: int) -> None:
        barrier.wait(timeout=10)
        for i in range(150):
            n = (thread * 3 + i * 5) % len(names)
            if i % 25 == 0:  # republish, so a hit races a replacement
                source.templates[names[n]] = text("x" * (10 * (n + 1) + i % 7))
            loader.get_template(names[n])

    with ThreadPoolExecutor(threads) as pool:
        for future in [pool.submit(work, thread) for thread in range(threads)]:
            future.result(timeout=60)  # raises what the thread raised

    assert len(loader._compiled.entries) <= 3
    assert loader._compiled.size <= budget
    assert_accounted(loader)


def test_a_deep_copied_loader_starts_with_an_empty_cache_of_its_own(
    counts: SimpleNamespace,
) -> None:
    """A deep-copied loader starts with an empty cache of its own.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
    loader = loader_for(Source({"one.xml": text("one"), "two.xml": text("two")}))
    first = loader.get_template("one.xml")
    loader.get_template("two.xml")
    size = loader._compiled.size
    assert counts.compiled.call_count == 2

    duplicate = copy.deepcopy(loader)

    assert duplicate is not loader
    assert duplicate.engine is not loader.engine
    assert duplicate.resolver is not loader.resolver
    assert duplicate._compiled is not loader._compiled
    assert duplicate._compiled.lock is not loader._compiled.lock
    assert_accounted(duplicate, entries=0)
    # A render through the copy compiles again and leaves the original alone.
    copied = duplicate.get_template("one.xml")
    assert counts.compiled.call_count == 3
    assert copied is not first
    assert copied.origin.loader is duplicate
    assert_accounted(duplicate, entries=1)
    assert_accounted(loader, entries=2)
    assert loader._compiled.size == size
    assert loader.get_template("one.xml") is first
    assert counts.compiled.call_count == 3


def test_the_compile_cache_deep_copies_empty_and_independent(
    counts: SimpleNamespace,
) -> None:
    """The compile cache is deep-copied empty and independent.

    Args:
        counts: Counters of the Django compiles and of the source validations.
    """
    loader = loader_for(Source({"one.xml": text("one")}))
    loader.get_template("one.xml")
    cache = loader._compiled
    assert cache.size == len(text("one"))

    duplicate = copy.deepcopy(cache)

    assert isinstance(duplicate, _CompiledCache)
    assert duplicate is not cache
    assert duplicate.entries is not cache.entries
    assert duplicate.lock is not cache.lock
    assert len(duplicate.entries) == 0
    assert duplicate.size == 0
    assert len(cache.entries) == 1
    assert cache.size == len(text("one"))


def test_a_deep_copied_loader_stays_paired_with_the_engine_that_owns_it() -> None:
    """A deep-copied loader stays paired with the engine that owns it."""
    engine = engine_for(Source({"screen.xml": text("one")}))
    loader = engine.backend.engine.template_loaders[0]

    duplicate = copy.deepcopy(loader)

    assert duplicate is not loader
    assert duplicate.engine is not loader.engine
    assert duplicate.engine.template_loaders == [duplicate]


def test_a_deep_copied_default_engine_has_a_cold_cache_of_its_own() -> None:
    """A deep-copied default engine has a cold cache of its own."""
    with override_settings(HYPERVIEW={"SOURCES": STUB_SOURCES}):
        engine = _default_engine()
        original = engine.backend.engine.template_loaders[0]
        assert engine.render("screen.xml") == text("hv")

        duplicate = copy.deepcopy(engine)

        loader = duplicate.backend.engine.template_loaders[0]
        assert isinstance(loader, ResolverLoader)
        assert loader is not original
        assert loader.engine is duplicate.backend.engine
        assert loader._compiled.lock is not original._compiled.lock
        assert_accounted(loader, entries=0)
        assert duplicate.render("other.xml") == text("hv")
        assert_accounted(loader, entries=1)
        assert_accounted(original, entries=1)
        assert engine.render("screen.xml") == text("hv")
        assert original.get_template("screen.xml").origin.loader is original


def test_a_deep_copied_compiled_template_renders_through_its_own_loader() -> None:
    """A deep-copied compiled template renders through its own loader."""
    with override_settings(HYPERVIEW={"SOURCES": STUB_SOURCES}):
        template = _default_engine().get_template("screen.xml")
        original = template.origin.loader
        assert_accounted(original, entries=1)

        duplicate = copy.deepcopy(template)

        loader = duplicate.origin.loader
        assert duplicate is not template
        assert loader is not original
        assert loader is duplicate.template.engine.template_loaders[0]
        assert duplicate.render() == template.render() == text("hv")
        assert_accounted(loader, entries=0)
        assert_accounted(original, entries=1)


class SlotBase(ResolverLoader):
    """An intermediate subclass that keeps a setting in a slot."""

    __slots__ = ("inherited",)


class SlotLoader(SlotBase):
    """A subclass that keeps settings in slots: its own, a private and inherited."""

    __slots__ = ("label", "__private", "unset")

    def get_template(
        self, template_name: str, skip: list[Origin] | None = None
    ) -> DjangoTemplate:
        """Load a template after reading each slot, so a lost slot fails here.

        Args:
            template_name: Name of the template to load.
            skip: Origins that the lookup skips, or None.

        Returns:
            The template that the loader finds.
        """
        # A real subclass reads its settings while it loads: a lost slot fails here.
        _ = (self.label, self.__private, self.inherited)
        return super().get_template(template_name, skip)


class StatefulLoader(ResolverLoader):
    """A subclass that chooses what its state holds and how it is restored."""

    def __getstate__(self) -> dict[str, Any]:
        """Return the state without the transient field and with a marker."""
        state = dict(super().__getstate__())
        del state["scratch"]  # transient: not part of the state
        state["saved"] = ["by-getstate"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore the state and mark the loader as restored."""
        self.__dict__ = {**state, "restored": True}


def warm(loader: ResolverLoader) -> ResolverLoader:
    """Load one template so the loader cache is warm.

    Args:
        loader: Loader whose cache is checked.

    Returns:
        The same loader, after it loaded a template.
    """
    loader.get_template("one.xml")
    return loader


def slotted_loader() -> SlotLoader:
    """Build a warm loader that keeps its settings in slots.

    Returns:
        A warm loader whose slots hold lists.
    """
    source = Source({"one.xml": text("one"), "two.xml": text("two")})
    loader = SlotLoader(Engine(), TemplateResolver([source]), ValidationSettings())
    loader.label = ["own"]
    loader._SlotLoader__private = ["private"]
    loader.inherited = ["inherited"]
    return warm(loader)


def assert_an_independent_copy(
    loader: ResolverLoader, duplicate: ResolverLoader
) -> None:
    """The copy renders with a cache and lock of its own; the original is as it was.

    Args:
        loader: Loader whose cache is checked.
        duplicate: Deep copy of the loader.
    """
    first = loader.get_template("one.xml")
    size = loader._compiled.size
    assert duplicate is not loader
    assert duplicate._compiled.lock is not loader._compiled.lock
    assert_accounted(duplicate, entries=0)

    template = duplicate.get_template("one.xml")

    assert template.render(Context()) == text("one")
    assert template.origin.loader is duplicate
    assert_accounted(duplicate, entries=1)
    assert_accounted(loader, entries=1)
    assert loader._compiled.size == size
    assert loader.get_template("one.xml") is first


@pytest.mark.parametrize(
    "slot",
    ["label", "_SlotLoader__private", "inherited"],
    ids=["own", "private", "inherited"],
)
def test_a_deep_copy_keeps_the_slots_of_a_subclass_as_deep_copies(slot: str) -> None:
    """A deep copy keeps the slots of a subclass as deep copies.

    Args:
        slot: Name of the slot whose value is checked in the copy.
    """
    loader = slotted_loader()
    value = getattr(loader, slot)

    duplicate = copy.deepcopy(loader)

    assert getattr(duplicate, slot) == value
    assert getattr(duplicate, slot) is not value
    getattr(duplicate, slot).append("changed")
    assert getattr(loader, slot) == value
    assert len(value) == 1
    assert_an_independent_copy(loader, duplicate)


def test_a_slot_left_unset_stays_unset_in_the_copy() -> None:
    """A slot left unset stays unset in the copy."""
    loader = slotted_loader()
    assert not hasattr(loader, "unset")

    duplicate = copy.deepcopy(loader)

    assert not hasattr(duplicate, "unset")
    assert_an_independent_copy(loader, duplicate)


def test_a_deep_copy_keeps_the_slots_and_the_dict_of_a_subclass() -> None:
    """A deep copy keeps the slots and the dict of a subclass."""
    loader = slotted_loader()
    loader.extra = ["in the dict"]

    duplicate = copy.deepcopy(loader)

    assert duplicate.extra == ["in the dict"]
    assert duplicate.extra is not loader.extra
    assert duplicate.resolver is not loader.resolver
    assert duplicate.engine is not loader.engine
    assert_an_independent_copy(loader, duplicate)


def test_a_deep_copy_honours_the_state_methods_of_a_subclass() -> None:
    """A deep copy honours the state methods of a subclass."""
    source = Source({"one.xml": text("one")})
    loader = StatefulLoader(Engine(), TemplateResolver([source]), ValidationSettings())
    loader.scratch = ["transient"]
    loader.tags = ["kept"]
    warm(loader)

    duplicate = copy.deepcopy(loader)

    assert not hasattr(duplicate, "scratch")
    assert duplicate.saved == ["by-getstate"]
    assert duplicate.restored is True
    assert duplicate.tags == ["kept"]
    assert duplicate.tags is not loader.tags
    assert loader.scratch == ["transient"]
    assert not hasattr(loader, "saved")
    assert not hasattr(loader, "restored")
    assert_an_independent_copy(loader, duplicate)


def rebuild(cls: type[ResolverLoader], tag: str) -> ResolverLoader:
    """Rebuild a loader from its tag, as pickle does.

    Args:
        cls: Loader class to rebuild.
        tag: Tag that the loader keeps.

    Returns:
        A new loader that is marked as rebuilt.
    """
    loader = cls.__new__(cls, tag=tag)
    loader.rebuilt = True
    return loader


class ReducedLoader(ResolverLoader):
    """A subclass that needs an argument to be created and rebuilds its own copies.

    Args:
        engine: Django engine that owns the loader.
        resolver: Resolver that the loader reads from.
        validation: Validation settings of the loader.
        tag: Tag that the loader keeps and that rebuilds its copies.
    """

    def __new__(cls, *args: Any, tag: str) -> "ReducedLoader":
        """Create the loader and keep its tag before initialization."""
        loader = super().__new__(cls)
        loader.tag = tag
        return loader

    def __init__(
        self,
        engine: Engine,
        resolver: TemplateResolver,
        validation: ValidationSettings | None,
        *,
        tag: str,
    ) -> None:
        """Initialize the loader and ignore the tag, which __new__ keeps."""
        super().__init__(engine, resolver, validation)

    def __reduce_ex__(self, protocol: int) -> tuple[Any, ...]:
        """Rebuild the loader through rebuild with its tag and its state."""
        return rebuild, (type(self), self.tag), self.__getstate__()


def test_a_deep_copy_honours_the_reduce_method_and_the_new_arguments_of_a_subclass() -> None:  # fmt: skip  # noqa: E501
    """A deep copy honours the reduce method and the arguments of a subclass."""
    source = Source({"one.xml": text("one")})
    loader = ReducedLoader(
        Engine(), TemplateResolver([source]), ValidationSettings(), tag="kept"
    )
    loader.tags = ["state"]
    warm(loader)

    duplicate = copy.deepcopy(loader)

    assert duplicate.rebuilt is True
    assert not hasattr(loader, "rebuilt")
    assert duplicate.tag == "kept"
    assert duplicate.tags == ["state"]
    assert duplicate.tags is not loader.tags
    assert_an_independent_copy(loader, duplicate)


class SetUpTestDataCopy(TestCase):
    """Django deep-copies class attributes of setUpTestData for every test."""

    databases = set()

    @classmethod
    def setUpTestData(cls) -> None:
        """Create the loader that the class shares and that each test copies."""
        cls.template = engine_for(Source({"screen.xml": text("{{ v }}")})).get_template(
            "screen.xml"
        )

    def test_a_compiled_template_can_be_used_in_the_first_test(self) -> None:
        """A compiled template can be used in the first test."""
        assert self.template.render({"v": 1}) == text("1")
        assert len(self.template.origin.loader._compiled.entries) == 0

    def test_a_compiled_template_can_be_used_in_the_second_test(self) -> None:
        """A compiled template can be used in the second test."""
        assert self.template.render({"v": 2}) == text("2")
        assert len(self.template.origin.loader._compiled.entries) == 0
