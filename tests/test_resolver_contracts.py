"""The resolver reports a silent cache and a source that breaks its contract."""

import json
import logging
from dataclasses import dataclass

import pytest
from django.test import override_settings

from dj_hyperview.cache import TemplateCache
from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

LOGGER = "dj_hyperview.cache"
LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "resolver-contracts",
    }
}
SECRET_NAME = "secret-screen.xml"


class Source:
    """A cacheable source that returns whatever its test tells it to."""

    cacheable = True

    def __init__(self, *, revision: object = "r1", content: object = "<view/>"):
        self.revision = revision
        self.content = content
        self.calls = 0

    def resolve(self, name: str) -> ResolvedTemplate | None:
        self.calls += 1
        return ResolvedTemplate(
            name,
            self.content,  # type: ignore[arg-type]
            f"memory:{name}",
            "memory",
            self.revision,  # type: ignore[arg-type]
        )


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


@pytest.mark.parametrize(
    ("break_cache", "operation", "reason"),
    [
        ("get", "generation", "backend failure"),
        ("set", "write", "backend failure"),
        ("corrupt", "read", "invalid payload"),
    ],
)
@override_settings(CACHES=LOCMEM_CACHES)
def test_bypass_logs_each_swallowed_cache_failure_with_a_fixed_reason(
    monkeypatch, caplog, break_cache, operation, reason
) -> None:
    """A cache that never works must not be invisible; the log holds no secrets."""
    cache = TemplateCache("logged-bypass", alias="screens")
    source = Source()
    resolver = TemplateResolver([source], cache=cache, failure_mode="bypass")
    if break_cache == "corrupt":
        generation = cache.generation(SECRET_NAME)
        key = cache.key(
            resolver._source_ids[0], SECRET_NAME, cache._resolved_revision(generation)
        )
        cache.backend.set(key, "invalid-json")
    else:
        monkeypatch.setattr(cache.backend, break_cache, lambda *a, **k: 1 / 0)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert resolver.resolve(SECRET_NAME).content == "<view/>"

    records = [r for r in caplog.records if r.name == LOGGER]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].getMessage() == (
        f"template_cache_bypassed operation={operation} reason={reason}"
    )
    assert SECRET_NAME not in caplog.text
    assert resolver._source_ids[0] not in caplog.text
    assert "division by zero" not in caplog.text
    assert records[0].exc_info is None


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
def test_cache_initialization_failure_is_logged_only_when_it_is_bypassed(
    caplog, failure_mode
) -> None:
    caches = {"broken": {"BACKEND": "tests.test_cache_fail_closed.ExplodingInitCache"}}
    configured = {
        "SOURCES": [{"BACKEND": "tests.stubs.TemplateSource"}],
        "CACHE": {"ALIAS": "broken", "FAILURE_MODE": failure_mode},
    }
    with override_settings(CACHES=caches, HYPERVIEW=configured):
        with caplog.at_level(logging.INFO, logger=LOGGER):
            if failure_mode == "raise":
                with pytest.raises(SourceUnavailable):
                    TemplateResolver.from_settings()
            else:
                TemplateResolver.from_settings()

    expected = (
        []
        if failure_mode == "raise"
        else ["template_cache_bypassed operation=initialize reason=backend failure"]
    )
    assert _messages(caplog) == expected


@override_settings(CACHES=LOCMEM_CACHES)
def test_a_working_cache_logs_nothing(caplog) -> None:
    cache = TemplateCache("quiet", alias="screens")
    resolver = TemplateResolver([Source()], cache=cache)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        resolver.resolve("screen.xml")
        resolver.resolve("screen.xml")

    assert _messages(caplog) == []


@override_settings(CACHES=LOCMEM_CACHES)
def test_a_cacheable_source_with_unsupported_options_is_reported_once(caplog) -> None:
    """Options that cannot be fingerprinted silently disable caching for the source."""
    source = Source()
    source.opaque = object()  # type: ignore[attr-defined]
    cache = TemplateCache("unfingerprintable", alias="screens")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        resolver = TemplateResolver([source], cache=cache)

    assert resolver._source_ids == (None,)
    records = [r for r in caplog.records if r.name == LOGGER]
    assert [r.levelno for r in records] == [logging.INFO]
    assert records[0].getMessage() == (
        "template_source_uncacheable source_index=0 reason=unsupported_options"
    )


@override_settings(CACHES=LOCMEM_CACHES)
def test_a_source_that_is_not_cacheable_by_choice_is_not_reported(caplog) -> None:
    source = Source()
    source.cacheable = False
    source.opaque = object()  # type: ignore[attr-defined]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TemplateResolver([source], cache=TemplateCache("chosen", alias="screens"))
        TemplateResolver([Source()])

    assert _messages(caplog) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"revision": None},
        {"revision": 3},
        {"content": b"<view/>"},
        {"content": None},
    ],
    ids=["revision-none", "revision-int", "content-bytes", "content-none"],
)
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_a_resolved_template_with_non_text_fields_is_rejected_before_caching(
    bad, failure_mode
) -> None:
    """The cache reader rejects what the writer produced, so the writer never runs."""
    cache = TemplateCache(f"fields-{failure_mode}", alias="screens")
    source = Source(**bad)
    resolver = TemplateResolver([source], cache=cache, failure_mode=failure_mode)

    for _ in range(2):
        with pytest.raises(SourceUnavailable) as error:
            resolver.resolve("screen.xml")
        assert error.value.reason == "invalid resolved template"
        assert error.value.source.endswith("Source")
    assert source.calls == 2
    assert cache.get_resolved(resolver._source_ids[0], "screen.xml") is None


@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_a_resolved_template_for_another_name_is_rejected_before_caching(
    failure_mode,
) -> None:
    class Renamer:
        cacheable = True

        def resolve(self, name):
            return ResolvedTemplate("other.xml", "<view/>", "memory:x", "memory", "r1")

    cache = TemplateCache(f"renamed-{failure_mode}", alias="screens")
    resolver = TemplateResolver([Renamer()], cache=cache, failure_mode=failure_mode)

    for _ in range(2):
        with pytest.raises(SourceUnavailable, match="invalid resolved template"):
            resolver.resolve("screen.xml")


def test_the_contract_is_enforced_without_a_cache_too() -> None:
    """The rule belongs to the source contract, not to the cache configuration."""
    with pytest.raises(SourceUnavailable, match="invalid resolved template"):
        TemplateResolver([Source(revision=None)]).resolve("screen.xml")


def test_a_source_that_returns_something_else_is_rejected() -> None:
    class Wrong:
        def resolve(self, name):
            return {"name": name}

    with pytest.raises(SourceUnavailable, match="invalid resolved template"):
        TemplateResolver([Wrong()]).resolve("screen.xml")


@override_settings(CACHES=LOCMEM_CACHES)
def test_a_valid_text_subclass_is_still_cached() -> None:
    from django.utils.safestring import mark_safe

    cache = TemplateCache("safe-text", alias="screens")
    source = Source(content=mark_safe("<view/>"))
    # A str subclass among the options would itself make the source unfingerprintable.
    resolver = TemplateResolver([source], cache=cache, _source_ids=["source:text"])

    first = resolver.resolve("screen.xml")
    second = resolver.resolve("screen.xml")

    assert first.content == second.content == "<view/>"
    assert source.calls == 1


@dataclass(frozen=True, slots=True)
class TextExtra(ResolvedTemplate):
    """A subclass whose extra field is text, like the five base fields."""

    extra: str = "note"


@dataclass(frozen=True, slots=True)
class NumberExtra(ResolvedTemplate):
    """A subclass whose extra field is not text."""

    extra: int = 7


class SubclassSource(Source):
    def __init__(self, kind: type[ResolvedTemplate]) -> None:
        super().__init__()
        self.kind = kind

    def resolve(self, name: str) -> ResolvedTemplate | None:
        self.calls += 1
        return self.kind(name, "<view/>", f"memory:{name}", "memory", "r1")


@pytest.mark.parametrize("kind", [TextExtra, NumberExtra])
def test_a_resolved_template_subclass_is_served_without_a_cache(kind) -> None:
    """Only the five fields of the contract are checked; a subclass may add more."""
    result = TemplateResolver([SubclassSource(kind)]).resolve("screen.xml")

    assert isinstance(result, kind)
    assert result.extra == {TextExtra: "note", NumberExtra: 7}[kind]


@pytest.mark.parametrize("kind", [TextExtra, NumberExtra])
@pytest.mark.parametrize("failure_mode", ["bypass", "raise"])
@override_settings(CACHES=LOCMEM_CACHES)
def test_a_resolved_template_subclass_is_cached_as_its_five_base_fields(
    kind, failure_mode
) -> None:
    """The cache reader accepts exactly five fields, so the writer must send those."""
    cache = TemplateCache(f"subclass-{kind.__name__}-{failure_mode}", alias="screens")
    source = SubclassSource(kind)
    resolver = TemplateResolver([source], cache=cache, failure_mode=failure_mode)

    first = resolver.resolve("screen.xml")
    second = resolver.resolve("screen.xml")

    assert source.calls == 1
    base = ("screen.xml", "<view/>", "memory:screen.xml", "memory", "r1")
    assert (first.name, first.content, first.origin, first.source, first.revision) == (
        base
    )
    assert second == ResolvedTemplate(*base)
    generation = cache.generation("screen.xml")
    key = cache.key(
        resolver._source_ids[0], "screen.xml", cache._resolved_revision(generation)
    )
    payload = json.loads(cache.backend.get(key))
    assert set(payload["template"]) == {
        "name",
        "content",
        "origin",
        "source",
        "revision",
    }
