"""Database router for package-owned multi-database acceptance."""

from typing import Any

from django.db.models import Model

READ_ALIAS = "default"
WRITE_ALIAS = "default"
_TEMPLATE_LABEL = "dj_hyperview_database.hyperviewtemplate"


class ConsumerDatabaseRouter:
    """Route only the optional Hyperview model to the selected test alias."""

    def db_for_read(self, model: type[Model], **hints: Any) -> str | None:
        """Choose the configured template read alias.

        Args:
            model: Model considered for a read.
            **hints: Routing hints supplied by Django.

        Returns:
            Selected alias for the template model, otherwise no preference.
        """
        del hints
        return READ_ALIAS if model._meta.label_lower == _TEMPLATE_LABEL else None

    def db_for_write(self, model: type[Model], **hints: Any) -> str | None:
        """Choose the configured template write alias.

        Args:
            model: Model considered for a write.
            **hints: Routing hints supplied by Django.

        Returns:
            Selected alias for the template model, otherwise no preference.
        """
        del hints
        return WRITE_ALIAS if model._meta.label_lower == _TEMPLATE_LABEL else None
