"""Controlled QuerySet mutations for database-backed templates."""

from collections.abc import Iterable
from typing import Any, cast

import django
from django.core import exceptions
from django.db import NotSupportedError, connections, models, transaction
from django.db.models import sql
from django.db.models.sql.constants import ROW_COUNT

from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.sources import canonicalize_template_name

from ._identity import (
    _assign_template_name_identity,
    _canonical_name_or_none,
    template_name_identity,
)
from ._invalidation import _schedule_invalidation
from ._mutation_context import batch_delete_primary_keys

_OBSERVABLE_FIELDS = frozenset({"name", "content", "active", "revision"})


def _canonical_names(
    values: Iterable[object], *, ignore_invalid: bool = False
) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for value in values:
        canonical = _canonical_name_or_none(value)
        if canonical is None:
            if ignore_invalid:
                continue
            raise InvalidTemplateName(value)
        names[canonical] = None
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

    def bulk_create(
        self,
        objs: Iterable[models.Model],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: list[str] | None = None,
        unique_fields: list[str] | None = None,
    ) -> list[models.Model]:
        """Create rows with portable identities for their stored names.

        Args:
            objs: Unsaved template instances.
            batch_size: Maximum rows per insert statement.
            ignore_conflicts: Whether supported constraint conflicts are ignored.
            update_conflicts: Unsupported; updates require publication services.
            update_fields: Fields updated for conflict handling.
            unique_fields: Fields identifying conflicts.

        Returns:
            The created template instances.

        Raises:
            NotSupportedError: If conflict updates are requested.
            ValueError: If Django rejects the bulk operation options.
            DatabaseError: If persistence fails.
        """
        if update_conflicts:
            raise NotSupportedError(
                "Hyperview bulk conflict updates are unsupported; "
                "use the publication services instead."
            )
        prepared = list(objs)
        for template in prepared:
            canonicalize_template_name(template.name)
            _assign_template_name_identity(template)
        created = models.QuerySet.bulk_create(
            self,
            prepared,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )
        names = _canonical_names(template.name for template in prepared)
        if names:
            _schedule_invalidation(*names, using=self.db)
        return created

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
        literal_rename = "name" in kwargs and type(kwargs["name"]) is str
        update_values = dict(kwargs)
        literal_name = None
        if literal_rename:
            literal_name = canonicalize_template_name(kwargs["name"])
            update_values["name_identity"] = template_name_identity(literal_name)
        query = _prepare_update(self, update_values)
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
            parameter_budget = connections[using].ops.bulk_batch_size(
                [primary_key], primary_keys
            )
            expression_rename = "name" in kwargs and not literal_rename
            reserved_parameters = len(update_values)
            parameters_per_row = 3 if expression_rename else 1
            batch_size = max(
                1,
                (parameter_budget - reserved_parameters) // parameters_per_row,
            )
            primary_key_batches = tuple(
                primary_keys[offset : offset + batch_size]
                for offset in range(0, len(primary_keys), batch_size)
            )
            updated = 0
            resolved_new_names: list[str] = []
            for batch in primary_key_batches:
                batch_query = query.clone()
                batch_query.clear_where()
                batch_query.add_q(models.Q(pk__in=batch))
                batch_updated = _execute_update(batch_query, using)
                updated += batch_updated
                if not batch_updated or not expression_rename:
                    continue
                renamed_rows = tuple(
                    self.model._base_manager.using(using)
                    .filter(pk__in=batch)
                    .order_by(primary_key.name)
                    .values_list(primary_key.name, "name")
                )
                batch_names = _canonical_names(name for _, name in renamed_rows)
                resolved_new_names.extend(batch_names)
                identity = models.Case(
                    *(
                        models.When(
                            pk=row_primary_key,
                            then=models.Value(template_name_identity(name)),
                        )
                        for row_primary_key, name in renamed_rows
                    ),
                    output_field=models.CharField(max_length=64),
                )
                self.model._base_manager.using(using).filter(pk__in=batch).update(
                    name_identity=identity
                )
            self._result_cache = None
            if not updated:
                return updated
            new_names = old_names
            if literal_name is not None:
                new_names = (literal_name,)
            elif expression_rename:
                new_names = tuple(dict.fromkeys(resolved_new_names))
            affected_names = tuple(dict.fromkeys((*old_names, *new_names)))
            if affected_names:
                _schedule_invalidation(*affected_names, using=using)
        return updated

    def delete(self) -> tuple[int, dict[str, int]]:
        """Delete selected rows with one locked invalidation snapshot.

        Returns:
            Total deleted objects and per-model deletion counts.

        Raises:
            NotSupportedError: If the QuerySet combines multiple queries.
            TypeError: If slicing, field-specific distinct, or values are used.
            DatabaseError: If selection or deletion SQL fails.
        """
        self._for_write = True
        using = self.db
        primary_key = self.model._meta.pk
        snapshot = self.using(using).all()
        snapshot.query.distinct = False
        snapshot.query.distinct_fields = ()
        with transaction.atomic(using=using):
            rows = tuple(
                snapshot.select_for_update()
                .order_by(primary_key.name)
                .values_list(primary_key.name, "name")
            )
            names = _canonical_names((name for _, name in rows), ignore_invalid=True)
            token = batch_delete_primary_keys.set(
                frozenset((using, row_primary_key) for row_primary_key, _ in rows)
            )
            try:
                deleted = models.QuerySet.delete(self.using(using))
            finally:
                batch_delete_primary_keys.reset(token)
            self._result_cache = None
            if deleted[0] and names:
                _schedule_invalidation(*names, using=using)
        return deleted


_ManagerBase = models.Manager.from_queryset(HyperviewTemplateQuerySet)


class HyperviewTemplateManager(_ManagerBase):
    """Expose controlled template QuerySet mutations through the manager."""
