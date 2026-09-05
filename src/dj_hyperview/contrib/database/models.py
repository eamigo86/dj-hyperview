"""Database model for optional Hyperview template storage."""

from typing import Any

from django.core.validators import MinValueValidator
from django.db import models

from ._identity import template_name_identity
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
        if self._state.adding or update_fields is None or "name" in update_fields:
            self.name_identity = template_name_identity(self.name)
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "name_identity"}
        super().save(*args, **kwargs)
