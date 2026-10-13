"""Connect database model mutations to transactional invalidation."""

from collections.abc import Callable
from dataclasses import dataclass

from django.apps import apps
from django.db import connections
from django.db.models import Model
from django.db.models.signals import (
    class_prepared,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)

from dj_hyperview.sources import canonicalize_template_name

from ._identity import _assign_template_name_identity, _canonical_name_or_none
from ._invalidation import _schedule_invalidation
from ._mutation_context import batch_delete_primary_keys
from .models import HyperviewTemplate

_STATE_ATTRIBUTE = "_dj_hyperview_invalidation_state"
_OBSERVABLE_FIELDS = frozenset({"name", "content", "active", "revision"})
_BATCH_DELETE = object()


@dataclass(frozen=True, slots=True)
class _MutationState:
    using: str
    names: tuple[str, ...]


def _persisted_name(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    using: str,
) -> str | None:
    if instance.pk is None:
        return None
    persisted = sender._base_manager.using(using).filter(pk=instance.pk)
    if connections[using].in_atomic_block:
        persisted = persisted.select_for_update()
    name = persisted.values_list("name", flat=True).first()
    return _canonical_name_or_none(name)


def _capture_save(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    using: str,
    raw: bool,
    update_fields: frozenset[str] | None,
    **kwargs: object,
) -> None:
    del kwargs
    instance.__dict__.pop(_STATE_ATTRIBUTE, None)
    if raw:
        _assign_template_name_identity(instance, update_fields)
    if update_fields is not None and _OBSERVABLE_FIELDS.isdisjoint(update_fields):
        return

    old_name = _persisted_name(sender, instance, using)
    if update_fields is not None and "name" not in update_fields:
        new_name = old_name
    elif raw:
        new_name = _canonical_name_or_none(instance.name)
    else:
        new_name = canonicalize_template_name(instance.name)
    if new_name is None:
        return
    names = (new_name,) if old_name in {None, new_name} else (old_name, new_name)
    instance.__dict__[_STATE_ATTRIBUTE] = _MutationState(using, names)


def _schedule_save(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    **kwargs: object,
) -> None:
    del sender, kwargs
    state = instance.__dict__.pop(_STATE_ATTRIBUTE, None)
    if isinstance(state, _MutationState):
        _schedule_invalidation(*state.names, using=state.using)


def _capture_delete(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    using: str,
    **kwargs: object,
) -> None:
    del kwargs
    instance.__dict__.pop(_STATE_ATTRIBUTE, None)
    batch = batch_delete_primary_keys.get()
    if batch is not None and (using, instance.pk) in batch:
        instance.__dict__[_STATE_ATTRIBUTE] = _BATCH_DELETE
        return
    persisted_name = _persisted_name(sender, instance, using)
    name = _canonical_name_or_none(
        instance.name if persisted_name is None else persisted_name
    )
    if name is not None:
        instance.__dict__[_STATE_ATTRIBUTE] = _MutationState(using, (name,))


def _schedule_delete(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    **kwargs: object,
) -> None:
    _schedule_save(sender, instance, **kwargs)


def _for_templates(handler: Callable[..., None]) -> Callable[..., None]:
    """Limit a model signal handler to the template model and its subclasses.

    Django 5.2 keeps no reference to a sender, so the registration of a collected
    class can match an unrelated class that later reuses its id.
    """

    def receiver(*, sender: object, **kwargs: object) -> None:
        if isinstance(sender, type) and issubclass(sender, HyperviewTemplate):
            handler(sender=sender, **kwargs)

    return receiver


_RECEIVERS = (
    (pre_save, _for_templates(_capture_save), "capture_save"),
    (post_save, _for_templates(_schedule_save), "schedule_save"),
    (pre_delete, _for_templates(_capture_delete), "capture_delete"),
    (post_delete, _for_templates(_schedule_delete), "schedule_delete"),
)


def _connect_model(model: type[Model]) -> None:
    """Connect the invalidation handlers to one template model class.

    Django sends the signals of a proxy instance with the proxy class as sender, so
    each class needs its own registration. A receiver without a sender would listen
    to every model, and Django skips its bulk delete for any model that has a
    pre_delete or post_delete listener.
    """
    for signal, receiver, name in _RECEIVERS:
        signal.connect(
            receiver,
            sender=model,
            dispatch_uid=f"dj_hyperview.database.{name}:{model._meta.label}",
            weak=False,
        )


def _connect_template_model(sender: type[Model], **kwargs: object) -> None:
    del kwargs
    if issubclass(sender, HyperviewTemplate):
        _connect_model(sender)


def _connect_signal_handlers() -> None:
    class_prepared.connect(
        _connect_template_model,
        dispatch_uid="dj_hyperview.database.connect_template_model",
        weak=False,
    )
    _connect_model(HyperviewTemplate)
    for model in apps.get_models():
        _connect_template_model(model)
