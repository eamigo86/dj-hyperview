"""Controlled QuerySet mutations for database-backed templates."""

from collections.abc import Iterable
from typing import Any

from django.db import models, transaction

from dj_hyperview.sources import canonicalize_template_name

from ._invalidation import _schedule_invalidation

_OBSERVABLE_FIELDS = frozenset({"name", "content", "active", "revision"})


def _canonical_names(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(canonicalize_template_name(value) for value in values))


class HyperviewTemplateQuerySet(models.QuerySet):
    """QuerySet that keeps raw-template cache invalidation commit-aware."""

    def update(self, **kwargs: Any) -> int:
        """Update selected rows and invalidate every affected template name.

        Args:
            **kwargs: Field values or expressions accepted by Django update.

        Returns:
            Number of rows matched by the database update.

        Raises:
            InvalidTemplateName: If a literal or resulting name is unsafe.
            ValueError: If the update attempts to modify the primary key.
            DatabaseError: If selection or update SQL fails.
        """
        self._for_write = True
        using = self.db
        queryset = self.using(using)
        primary_key = self.model._meta.pk
        if {"pk", primary_key.name, primary_key.attname} & kwargs.keys():
            raise ValueError("QuerySet.update cannot modify the primary key")
        if "name" in kwargs and type(kwargs["name"]) is str:
            canonicalize_template_name(kwargs["name"])
        if _OBSERVABLE_FIELDS.isdisjoint(kwargs):
            return models.QuerySet.update(queryset, **kwargs)

        with transaction.atomic(using=using):
            rows = tuple(
                queryset.select_for_update()
                .order_by(primary_key.name)
                .values_list(primary_key.name, "name")
            )
            old_names = _canonical_names(name for _, name in rows)
            primary_keys = tuple(row_primary_key for row_primary_key, _ in rows)
            authoritative = self.model._base_manager.using(using).filter(
                pk__in=primary_keys
            )
            updated = models.QuerySet.update(authoritative, **kwargs)
            if not updated:
                return updated
            new_names = _canonical_names(
                self.model._base_manager.using(using)
                .filter(pk__in=primary_keys)
                .order_by(primary_key.name)
                .values_list("name", flat=True)
            )
            affected_names = tuple(dict.fromkeys((*old_names, *new_names)))
            _schedule_invalidation(*affected_names, using=using)
        return updated


_ManagerBase = models.Manager.from_queryset(HyperviewTemplateQuerySet)


class HyperviewTemplateManager(_ManagerBase):
    """Expose controlled template QuerySet mutations through the manager."""
