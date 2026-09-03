import traceback
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.cache import caches
from django.db import connections
from django.test import override_settings

from dj_hyperview.cache import TemplateCache, invalidate_templates
from dj_hyperview.checks import check_hyperview_settings
from dj_hyperview.contrib.database.sources import DatabaseSource
from dj_hyperview.exceptions import (
    HyperviewConfigurationError,
    SourceUnavailable,
    TemplateNotFound,
)
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.sources import ResolvedTemplate

DATABASE_APPS = ["dj_hyperview", "dj_hyperview.contrib.database"]
DATABASE_BACKEND = "dj_hyperview.contrib.database.sources.DatabaseSource"
LOCMEM_CACHES = {
    "screens": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "database-source-isolation",
    }
}
ALIASES = ("default", "replica")
UNSET = object()


class SwitchingRouter:
    def __init__(self) -> None:
        self.selected = "default"
        self.reads: list[str] = []

    def db_for_read(self, model: type, **hints: object) -> str:
        del model, hints
        self.reads.append(self.selected)
        return self.selected


class UncachedSource:
    _dj_hyperview_cacheable = False

    def __init__(self) -> None:
        self.content = "first"
        self.calls = 0

    def resolve(self, name: str) -> ResolvedTemplate:
        self.calls += 1
        return ResolvedTemplate(name, self.content, "memory", "memory", "1")


@pytest.fixture
def dual_database_model(django_db_blocker):
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        model = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
        with django_db_blocker.unblock():
            for alias in ALIASES:
                with connections[alias].schema_editor() as editor:
                    editor.create_model(model)
        yield model
        with django_db_blocker.unblock():
            for alias in reversed(ALIASES):
                with connections[alias].schema_editor() as editor:
                    editor.delete_model(model)


def database_config(namespace: str, using: object = UNSET) -> dict[str, object]:
    options = {} if using is UNSET else {"using": using}
    return {
        "SOURCES": [{"BACKEND": DATABASE_BACKEND, "OPTIONS": options}],
        "CACHE": {"ALIAS": "screens", "NAMESPACE": namespace},
    }


@pytest.mark.parametrize(
    "using",
    [123, True, [], object(), "", "missing"],
    ids=["integer", "boolean", "list", "object", "empty", "unknown"],
)
def test_database_using_invalid_values_fail_safely_at_runtime(using: object) -> None:
    with override_settings(INSTALLED_APPS=DATABASE_APPS):
        source = DatabaseSource(using=using)
        with pytest.raises(SourceUnavailable) as captured:
            source.resolve("screen.xml")

    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert str(error) == "Template source unavailable: database (alias unavailable)"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "missing" not in rendered
    assert "object at" not in rendered


@pytest.mark.parametrize(
    "using",
    [123, True, [], object(), "", "missing"],
    ids=["integer", "boolean", "list", "object", "empty", "unknown"],
)
def test_database_using_invalid_values_have_actionable_checks(using: object) -> None:
    with override_settings(
        INSTALLED_APPS=DATABASE_APPS,
        HYPERVIEW=database_config("invalid-using", using),
        CACHES=LOCMEM_CACHES,
    ):
        errors = check_hyperview_settings()
        with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E011"):
            TemplateResolver.from_settings()

    assert [error.id for error in errors] == ["dj_hyperview.E011"]
    assert errors[0].msg == (
        "SOURCES[0].OPTIONS.using must be None or name a configured database."
    )


def test_database_using_check_does_not_apply_to_third_party_sources() -> None:
    configured = {
        "SOURCES": [
            {"BACKEND": "tests.stubs.TemplateSource", "OPTIONS": {"using": object()}}
        ]
    }
    with override_settings(HYPERVIEW=configured):
        assert check_hyperview_settings() == []


@override_settings(CACHES=LOCMEM_CACHES)
def test_private_source_marker_disables_generic_resolver_cache() -> None:
    source = UncachedSource()
    cache = TemplateCache("private-cache-opt-out", alias="screens")
    resolver = TemplateResolver([source], cache=cache)

    with patch.object(cache.backend, "get", wraps=cache.backend.get) as cache_get:
        first = resolver.resolve("screen.xml")
        source.content = "second"
        second = resolver.resolve("screen.xml")

    assert (first.content, second.content) == ("first", "second")
    assert source.calls == 2
    assert cache_get.call_count == 0


@pytest.mark.django_db(transaction=True, databases=ALIASES)
@pytest.mark.parametrize("cached_miss", [False, True], ids=["content", "miss"])
def test_router_selected_database_source_never_reuses_cross_database_cache(
    dual_database_model, cached_miss: bool
) -> None:
    if not cached_miss:
        dual_database_model.objects.using("default").create(
            name="screen.xml", content="default"
        )
    dual_database_model.objects.using("replica").create(
        name="screen.xml", content="replica"
    )
    router = SwitchingRouter()
    config = database_config(f"router-{cached_miss}")

    with override_settings(
        DATABASE_ROUTERS=[router], CACHES=LOCMEM_CACHES, HYPERVIEW=config
    ):
        caches["screens"].clear()
        if cached_miss:
            with pytest.raises(TemplateNotFound):
                TemplateResolver.from_settings().resolve("screen.xml")
        else:
            assert TemplateResolver.from_settings().resolve("screen.xml").content == (
                "default"
            )
        router.selected = "replica"
        resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert resolved.content == "replica"
    assert router.reads == ["default", "replica"]


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_explicit_database_aliases_isolate_cache_and_support_invalidation(
    dual_database_model,
) -> None:
    dual_database_model.objects.using("default").create(
        name="screen.xml", content="default-old", revision=1
    )
    dual_database_model.objects.using("replica").create(
        name="screen.xml", content="replica", revision=1
    )

    router = SwitchingRouter()
    router.selected = "replica"
    with override_settings(CACHES=LOCMEM_CACHES, DATABASE_ROUTERS=[router]):
        caches["screens"].clear()
        with override_settings(HYPERVIEW=database_config("explicit", "default")):
            first = TemplateResolver.from_settings().resolve("screen.xml")
            dual_database_model.objects.using("default").filter(
                name="screen.xml"
            ).update(content="default-new", revision=2)
            cached = TemplateResolver.from_settings().resolve("screen.xml")
        with override_settings(HYPERVIEW=database_config("explicit", "replica")):
            replica = TemplateResolver.from_settings().resolve("screen.xml")
        with override_settings(HYPERVIEW=database_config("explicit", "default")):
            invalidate_templates("screen.xml")
            refreshed = TemplateResolver.from_settings().resolve("screen.xml")

    assert (first.content, cached.content) == ("default-old", "default-old")
    assert replica.content == "replica"
    assert (refreshed.content, refreshed.revision) == ("default-new", "2")
    assert router.reads == []


@pytest.mark.django_db(transaction=True, databases=ALIASES)
def test_explicit_database_alias_negative_cache_is_isolated(
    dual_database_model,
) -> None:
    dual_database_model.objects.using("replica").create(
        name="screen.xml", content="replica"
    )

    with override_settings(CACHES=LOCMEM_CACHES):
        caches["screens"].clear()
        with override_settings(HYPERVIEW=database_config("explicit-miss", "default")):
            with pytest.raises(TemplateNotFound):
                TemplateResolver.from_settings().resolve("screen.xml")
        with override_settings(HYPERVIEW=database_config("explicit-miss", "replica")):
            resolved = TemplateResolver.from_settings().resolve("screen.xml")

    assert resolved.content == "replica"
