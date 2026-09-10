"""Public immutable events for committed database template mutations."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Concat
from django.test import override_settings

from tests.test_database_signals import dual_signal_model as dual_signal_model

pytestmark = pytest.mark.django_db(transaction=True, databases=("default", "replica"))


def test_event_is_public_and_snapshots_mutable_names() -> None:
    import dj_hyperview
    from dj_hyperview.signals import TemplateInvalidation, template_invalidated

    names = ["screens/old.xml", "screens/new.xml", "screens/old.xml"]
    event = TemplateInvalidation(names=names, using="replica")
    names[:] = ["later.xml"]

    assert event.names == frozenset({"screens/old.xml", "screens/new.xml"})
    assert event.using == "replica"
    assert dj_hyperview.TemplateInvalidation is TemplateInvalidation
    assert dj_hyperview.template_invalidated is template_invalidated
    with pytest.raises(FrozenInstanceError):
        event.using = "default"
    with pytest.raises(FrozenInstanceError):
        event.names = frozenset({"later.xml"})


def test_event_reuses_canonical_name_validation() -> None:
    from dj_hyperview.exceptions import InvalidTemplateName
    from dj_hyperview.signals import TemplateInvalidation

    with pytest.raises(InvalidTemplateName):
        TemplateInvalidation(names=["valid.xml", "../private.xml"], using="default")


@pytest.mark.parametrize("names", ["screen.xml", b"screen.xml"])
def test_event_does_not_treat_one_name_as_a_collection(names: object) -> None:
    from dj_hyperview.signals import TemplateInvalidation

    with pytest.raises(TypeError):
        TemplateInvalidation(names=names, using="default")


@pytest.mark.parametrize("using", [None, [], ""])
def test_event_requires_an_immutable_database_alias(using: object) -> None:
    from dj_hyperview.signals import TemplateInvalidation

    with pytest.raises(ValueError):
        TemplateInvalidation(names={"screen.xml"}, using=using)


@pytest.fixture
def received():
    from dj_hyperview.signals import template_invalidated

    events = []

    def receive(sender, event, **kwargs):
        events.append((sender, event, transaction.get_autocommit(using=event.using)))

    template_invalidated.connect(receive, weak=False)
    try:
        yield events
    finally:
        template_invalidated.disconnect(receive)


@pytest.mark.parametrize(
    ("operation", "names"),
    [
        ("create", {"new.xml"}),
        ("content", {"old.xml"}),
        ("active", {"old.xml"}),
        ("revision", {"old.xml"}),
        ("rename", {"old.xml", "renamed.xml"}),
        ("raw-save", {"old.xml"}),
        ("update", {"old.xml", "other.xml"}),
        ("expression-rename", {"old.xml", "other.xml", "p/old.xml", "p/other.xml"}),
        ("bulk-create", {"new.xml", "second.xml"}),
        ("bulk-ignore-conflicts", {"old.xml", "new.xml"}),
        ("bulk-update", {"old.xml", "other.xml"}),
        ("delete", {"old.xml"}),
        ("queryset-delete", {"old.xml", "other.xml"}),
        ("publish", {"new.xml"}),
        ("publish-edit", {"old.xml"}),
        ("publish-rename", {"old.xml", "renamed.xml"}),
        ("publish-delete", {"old.xml"}),
    ],
)
@override_settings(
    HYPERVIEW={},
    INSTALLED_APPS=["dj_hyperview", "dj_hyperview.contrib.database"],
)
def test_supported_mutations_emit_after_commit_without_cache(
    dual_signal_model, received, operation, names
):
    from dj_hyperview.contrib.database.services import (
        delete_template,
        publish_template,
        rename_template,
    )

    model = dual_signal_model
    first, second = model.objects.bulk_create(
        [model(name=name, content="<view />") for name in ("old.xml", "other.xml")]
    )
    received.clear()
    with transaction.atomic():
        if operation == "create":
            model.objects.create(name="new.xml", content="<view />")
        elif operation in {"content", "active", "revision", "rename"}:
            field, value = {
                "content": ("content", "<text />"),
                "active": ("active", False),
                "revision": ("revision", 2),
                "rename": ("name", "renamed.xml"),
            }[operation]
            setattr(first, field, value)
            first.save(update_fields={field})
        elif operation == "raw-save":
            first.content = "<text />"
            first.save_base(raw=True, using="default", update_fields={"content"})
        elif operation == "update":
            model.objects.update(content="<text />")
        elif operation == "expression-rename":
            model.objects.update(name=Concat(Value("p/"), F("name")))
        elif operation.startswith("bulk-") and operation != "bulk-update":
            ignored = operation == "bulk-ignore-conflicts"
            model.objects.bulk_create(
                [model(name=n, content="<view />") for n in sorted(names)],
                ignore_conflicts=ignored,
            )
        elif operation == "bulk-update":
            first.content = second.content = "<text />"
            model.objects.bulk_update([first, second], ["content"])
        elif operation == "delete":
            first.delete()
        elif operation == "queryset-delete":
            model.objects.all().delete()
        elif operation in {"publish", "publish-edit"}:
            name = "new.xml" if operation == "publish" else "old.xml"
            publish_template(name, "<view />", using="default")
        elif operation == "publish-rename":
            rename_template("old.xml", "renamed.xml", using="default")
        else:
            assert operation == "publish-delete"
            delete_template("old.xml", using="default")
        assert received == []

    assert len(received) == 1
    sender, event, committed = received[0]
    assert sender is model
    assert event.names == frozenset(names)
    assert event.using == "default"
    assert committed is True


def test_alias_and_names_remain_bound_to_the_mutation(dual_signal_model, received):
    with transaction.atomic(using="default"):
        with transaction.atomic(using="replica"):
            template = dual_signal_model.objects.using("replica").create(
                name="replica.xml", content="<view />"
            )
            template.name = "unsaved.xml"
            assert received == []
        assert [(event.names, event.using) for _, event, _ in received] == [
            (frozenset({"replica.xml"}), "replica")
        ]
        dual_signal_model.objects.create(name="default.xml", content="<view />")
        assert len(received) == 1
    assert received[-1][1].using == "default"
    assert all(committed for _, _, committed in received)


def test_rollback_and_irrelevant_or_empty_mutations_emit_nothing(
    dual_signal_model, received
):
    model = dual_signal_model
    template = model.objects.create(name="old.xml", content="<view />")
    received.clear()
    with transaction.atomic():
        template.save(update_fields={"updated_at"})
        model.objects.filter(pk=template.pk).update(updated_at=template.updated_at)
        model.objects.none().update(content="<text />")
        model.objects.none().delete()
        model.objects.bulk_create([])
        with pytest.raises(RuntimeError, match="savepoint"):
            with transaction.atomic():
                model.objects.create(name="nested.xml", content="<view />")
                raise RuntimeError("savepoint")
    with pytest.raises(RuntimeError, match="outer"):
        with transaction.atomic(using="replica"):
            model.objects.using("replica").create(name="outer.xml", content="<view />")
            raise RuntimeError("outer")
    assert received == []


@pytest.mark.parametrize("cache_fails", [False, True])
def test_same_callback_emits_after_cache_and_preserves_cache_exception(
    dual_signal_model, received, cache_fails
):
    from dj_hyperview.exceptions import SourceUnavailable
    from dj_hyperview.signals import template_invalidated

    failure = SourceUnavailable("cache:test", "controlled failure")
    order = []

    def cache(*names):
        assert names == ("new.xml",)
        order.append("cache")
        if cache_fails:
            raise failure

    def broken_receiver(**kwargs):
        order.append("broken receiver")
        raise RuntimeError("controlled receiver failure")

    def later_receiver(**kwargs):
        order.append("later receiver")

    template_invalidated.connect(broken_receiver, weak=False)
    template_invalidated.connect(later_receiver, weak=False)
    try:
        with (
            patch(
                "dj_hyperview.contrib.database._invalidation.invalidate_templates",
                side_effect=cache,
            ),
            patch(
                "dj_hyperview.contrib.database._invalidation.transaction.on_commit",
                wraps=transaction.on_commit,
            ) as register,
        ):

            def mutate():
                with transaction.atomic():
                    dual_signal_model.objects.create(name="new.xml", content="<view />")
                    assert order == []

            if cache_fails:
                with pytest.raises(SourceUnavailable) as caught:
                    mutate()
                assert caught.value is failure
            else:
                mutate()
            assert register.call_count == 1
            assert register.call_args.kwargs == {"using": "default", "robust": False}
    finally:
        template_invalidated.disconnect(broken_receiver)
        template_invalidated.disconnect(later_receiver)

    assert order == ["cache", "broken receiver", "later receiver"]
    assert len(received) == 1
    assert received[0][2] is True
    assert dual_signal_model.objects.filter(name="new.xml").exists()
