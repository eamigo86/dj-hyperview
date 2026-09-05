"""Controlled QuerySet mutations for database-backed templates."""

from collections.abc import Iterable
from typing import Any, cast

import django
from django.core import exceptions
from django.db import models, transaction
from django.db.models import sql
from django.db.models.sql.constants import ROW_COUNT

from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.sources import canonicalize_template_name

from ._invalidation import _schedule_invalidation

_OBSERVABLE_FIELDS = frozenset({"name", "content", "active", "revision"})


def _canonical_names(
    values: Iterable[object], *, ignore_invalid: bool = False
) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for value in values:
        try:
            names[canonicalize_template_name(value)] = None
        except InvalidTemplateName:
            if not ignore_invalid:
                raise
    return tuple(names)


def _prepare_update(
    queryset: models.QuerySet, values: dict[str, Any]
) -> sql.UpdateQuery:
    queryset._not_support_combined_queries("update")
    if queryset.query.is_sliced:
        raise TypeError("Cannot update a query once a slice has been taken.")
    if django.VERSION[:2] >= (6, 1) and queryset.query.distinct_fields:
        raise TypeError("Cannot call update() after .distinct(*fields).")
    queryset._for_write = True
    query = queryset.query.chain(sql.UpdateQuery)
    query.add_update_values(values)
    order_by: list[object] = []
    for column in query.order_by:
        alias = column
        descending = False
        if isinstance(alias, str) and alias.startswith("-"):
            alias = alias.removeprefix("-")
            descending = True
        if annotation := query.annotations.get(alias):
            if getattr(annotation, "contains_aggregate", False):
                raise exceptions.FieldError(
                    f"Cannot update when ordering by an aggregate: {annotation}"
                )
            order_by.append(annotation.desc() if descending else annotation)
        else:
            order_by.append(column)
    query.order_by = tuple(order_by)
    query.clear_select_clause()
    return query


def _execute_update(query: sql.UpdateQuery, using: str) -> int:
    with transaction.mark_for_rollback_on_error(using=using):
        return cast(int, query.get_compiler(using).execute_sql(ROW_COUNT))


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
            NotSupportedError: If the QuerySet combines multiple queries.
            TypeError: If slicing or field-specific distinct makes update invalid.
            FieldError: If fields or ordering expressions are invalid.
            ValueError: If the update attempts to modify the primary key.
            DatabaseError: If selection or update SQL fails.
        """
        primary_key = self.model._meta.pk
        if {"pk", primary_key.name, primary_key.attname} & kwargs.keys():
            _prepare_update(self, {})
            raise ValueError("QuerySet.update cannot modify the primary key")
        if _OBSERVABLE_FIELDS.isdisjoint(kwargs):
            return models.QuerySet.update(self, **kwargs)
        query = _prepare_update(self, kwargs)
        if "name" in kwargs and type(kwargs["name"]) is str:
            canonicalize_template_name(kwargs["name"])
        using = self.db
        if self.query.is_empty():
            updated = _execute_update(query, using)
            self._result_cache = None
            return updated
        queryset = self.using(using)

        with transaction.atomic(using=using):
            snapshot = queryset.all()
            snapshot.query.distinct = False
            snapshot.query.distinct_fields = ()
            rows = tuple(
                snapshot.select_for_update()
                .order_by(primary_key.name)
                .values_list(primary_key.name, "name")
            )
            old_names = _canonical_names(
                (name for _, name in rows), ignore_invalid=True
            )
            primary_keys = tuple(row_primary_key for row_primary_key, _ in rows)
            query.clear_where()
            query.add_q(models.Q(pk__in=primary_keys))
            updated = _execute_update(query, using)
            self._result_cache = None
            if not updated:
                return updated
            new_names = old_names
            if "name" in kwargs:
                new_names = _canonical_names(
                    self.model._base_manager.using(using)
                    .filter(pk__in=primary_keys)
                    .order_by(primary_key.name)
                    .values_list("name", flat=True)
                )
            affected_names = tuple(dict.fromkeys((*old_names, *new_names)))
            if affected_names:
                _schedule_invalidation(*affected_names, using=using)
        return updated


_ManagerBase = models.Manager.from_queryset(HyperviewTemplateQuerySet)


class HyperviewTemplateManager(_ManagerBase):
    """Expose controlled template QuerySet mutations through the manager."""
