"""Connect database model mutations to transactional invalidation."""

from dataclasses import dataclass

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from dj_hyperview.sources import canonicalize_template_name

from ._invalidation import _schedule_invalidation
from .models import HyperviewTemplate

_STATE_ATTRIBUTE = "_dj_hyperview_invalidation_state"
_OBSERVABLE_FIELDS = frozenset({"name", "content", "active", "revision"})


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
    name = (
        sender._default_manager.using(using)
        .filter(pk=instance.pk)
        .values_list("name", flat=True)
        .first()
    )
    return None if name is None else canonicalize_template_name(name)


def _capture_save(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    using: str,
    raw: bool,
    update_fields: frozenset[str] | None,
    **kwargs: object,
) -> None:
    del kwargs, raw
    instance.__dict__.pop(_STATE_ATTRIBUTE, None)
    if update_fields is not None and _OBSERVABLE_FIELDS.isdisjoint(update_fields):
        return

    old_name = _persisted_name(sender, instance, using)
    new_name = (
        old_name
        if old_name is not None
        and update_fields is not None
        and "name" not in update_fields
        else canonicalize_template_name(instance.name)
    )
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
    persisted_name = _persisted_name(sender, instance, using)
    name = canonicalize_template_name(
        instance.name if persisted_name is None else persisted_name
    )
    instance.__dict__[_STATE_ATTRIBUTE] = _MutationState(using, (name,))


def _schedule_delete(
    sender: type[HyperviewTemplate],
    instance: HyperviewTemplate,
    **kwargs: object,
) -> None:
    _schedule_save(sender, instance, **kwargs)


def _connect_signal_handlers() -> None:
    pre_save.connect(
        _capture_save,
        sender=HyperviewTemplate,
        dispatch_uid="dj_hyperview.database.capture_save",
        weak=False,
    )
    post_save.connect(
        _schedule_save,
        sender=HyperviewTemplate,
        dispatch_uid="dj_hyperview.database.schedule_save",
        weak=False,
    )
    pre_delete.connect(
        _capture_delete,
        sender=HyperviewTemplate,
        dispatch_uid="dj_hyperview.database.capture_delete",
        weak=False,
    )
    post_delete.connect(
        _schedule_delete,
        sender=HyperviewTemplate,
        dispatch_uid="dj_hyperview.database.schedule_delete",
        weak=False,
    )
