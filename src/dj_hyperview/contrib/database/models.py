"""Database model for optional Hyperview template storage."""

from collections.abc import Collection
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import DEFAULT_DB_ALIAS, models, router, transaction

from dj_hyperview.sources import canonicalize_template_name

from ._identity import _assign_template_name_identity, template_name_identity
from .querysets import HyperviewTemplateManager
from .validators import (
    validate_canonical_template_name,
    validate_stored_template_source,
)


class HyperviewTemplate(models.Model):
    """An optional, validated source template stored by canonical name."""

    name = models.CharField(
        max_length=255, validators=[validate_canonical_template_name]
    )
    name_identity = models.CharField(max_length=64, unique=True, editable=False)
    content = models.TextField(validators=[validate_stored_template_source])
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = HyperviewTemplateManager()

    class Meta:
        app_label = "dj_hyperview_database"
        ordering = ("name",)
        verbose_name = "Hyperview template"
        verbose_name_plural = "Hyperview templates"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(revision__gte=1),
                name="djhv_template_revision_gte_1",
            )
        ]

    def __str__(self) -> str:
        """Return the canonical template name.

        Returns:
            Canonical name stored for this template.
        """
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the byte-exact identity whenever the name can change.

        Args:
            *args: Positional arguments forwarded to Django's model save.
            **kwargs: Keyword arguments forwarded to Django's model save.

        Raises:
            DatabaseError: If persistence fails.
        """
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = frozenset(update_fields)
            kwargs["update_fields"] = update_fields
        if self._state.adding or update_fields is None or "name" in update_fields:
            canonicalize_template_name(self.name)
            _assign_template_name_identity(self, update_fields)
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "name_identity"}
        requested_alias = kwargs.get("using")
        alias = (
            requested_alias
            if requested_alias is not None
            else router.db_for_write(type(self), instance=self) or DEFAULT_DB_ALIAS
        )
        kwargs["using"] = alias
        with transaction.atomic(using=alias, savepoint=False):
            super().save(*args, **kwargs)

    def validate_unique(self, exclude: Collection[str] | None = None) -> None:
        """Validate byte-exact name uniqueness through the public name field.

        Args:
            exclude: Model fields omitted from uniqueness validation.

        Raises:
            ValidationError: If another row owns the exact template name.
        """
        excluded = set(exclude or ())
        if "name" in excluded:
            excluded.add("name_identity")
        else:
            self.name_identity = template_name_identity(self.name)
            excluded.discard("name_identity")

        try:
            super().validate_unique(exclude=excluded)
        except ValidationError as error:
            errors = dict(error.error_dict)
            identity_errors = errors.pop("name_identity", ())
            if identity_errors:
                errors.setdefault("name", []).extend(
                    ValidationError(
                        "A template with this name already exists.",
                        code=identity_error.code or "unique",
                    )
                    for identity_error in identity_errors
                )
            raise ValidationError(errors) from None
