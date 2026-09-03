"""Optional database-backed template source."""

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import DatabaseError
from django.utils.connection import ConnectionDoesNotExist

from dj_hyperview.exceptions import SourceUnavailable
from dj_hyperview.sources import ResolvedTemplate, canonicalize_template_name

APP_LABEL = "dj_hyperview_database"
MODEL_NAME = "HyperviewTemplate"
SOURCE = "database"
_QUERY_FAILED = object()

__all__ = ["DatabaseSource"]


def _template_model():
    try:
        return apps.get_model(APP_LABEL, MODEL_NAME)
    except (AppRegistryNotReady, LookupError):
        return None


def _get_active(manager, model, name):
    try:
        return manager.get(name=name, active=True)
    except model.DoesNotExist:
        return None
    except (ConnectionDoesNotExist, DatabaseError):
        return _QUERY_FAILED


class DatabaseSource:
    """Resolve raw templates from the optional database application."""

    def __init__(self, *, using: str | None = None) -> None:
        self.using = using

    def resolve(self, name: str) -> ResolvedTemplate | None:
        canonical = canonicalize_template_name(name)
        model = _template_model()
        if model is None:
            raise SourceUnavailable(SOURCE, "app unavailable")

        manager = model._default_manager
        if self.using is not None:
            manager = manager.using(self.using)
        template = _get_active(manager, model, canonical)
        if template is _QUERY_FAILED:
            raise SourceUnavailable(SOURCE, "query failed")
        if template is None:
            return None
        return ResolvedTemplate(
            name=canonical,
            content=template.content,
            origin=f"database:{canonical}",
            source=SOURCE,
            revision=str(template.revision),
        )
