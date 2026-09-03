"""Validated publication services for optional database templates."""

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import IntegrityError, router, transaction

from dj_hyperview.exceptions import HyperviewError, SourceUnavailable
from dj_hyperview.sources import canonicalize_template_name

from ._config import _database_alias_is_configured

SOURCE = "database"

__all__ = ["PublicationConflict", "PublicationResult", "publish_template"]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Describe one successfully committed template mutation.

    Attributes:
        name: Canonical template name.
        revision: Persisted revision after publication.
        created: Whether publication created a new row.
    """

    name: str
    revision: int
    created: bool


class PublicationConflict(HyperviewError):
    """A template publication could not satisfy its revision contract."""

    def __init__(self) -> None:
        super().__init__("Template publication conflict")


def _template_model() -> type[Any] | None:
    try:
        return apps.get_model("dj_hyperview_database", "HyperviewTemplate")
    except (AppRegistryNotReady, LookupError):
        return None


def _database_alias(model: type[Any], using: object) -> str:
    alias = using if using is not None else router.db_for_write(model)
    if not _database_alias_is_configured(alias) or alias is None:
        raise SourceUnavailable(SOURCE, "alias unavailable")
    return alias


def publish_template(
    name: str,
    content: str,
    *,
    active: bool = True,
    expected_revision: int | None = None,
    using: str | None = None,
) -> PublicationResult:
    """Create or update one validated database template atomically.

    Args:
        name: Canonical consumer template name.
        content: Raw consumer-owned template source.
        active: Whether database resolution may return the template.
        expected_revision: Existing revision required for optimistic publication.
        using: Explicit database alias, or None to use Django write routing.

    Returns:
        Immutable metadata for the persisted publication.

    Raises:
        InvalidTemplateName: If the template name is unsafe or noncanonical.
        ValueError: If expected_revision is not a positive integer or None.
        ValidationError: If model field validation rejects the publication.
        PublicationConflict: If the expected revision is absent or stale, or a
            concurrent create wins the unique-name race.
        SourceUnavailable: If the optional app or database alias is unavailable.
        DatabaseError: If database access fails.
    """
    canonical = canonicalize_template_name(name)
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer or None")
    if using is not None and not _database_alias_is_configured(using):
        raise SourceUnavailable(SOURCE, "alias unavailable")
    model = _template_model()
    if model is None:
        raise SourceUnavailable(SOURCE, "app unavailable")
    alias = _database_alias(model, using)
    conflict = False
    result: PublicationResult

    with transaction.atomic(using=alias):
        manager = model._default_manager.using(alias)
        try:
            template = manager.select_for_update().get(name=canonical)
        except model.DoesNotExist:
            template = None
        if template is None:
            if expected_revision is not None:
                raise PublicationConflict
            template = model(name=canonical, content=content, active=active, revision=1)
            created = True
        else:
            if expected_revision is not None and template.revision != expected_revision:
                raise PublicationConflict
            template.content = content
            template.active = active
            template.revision += 1
            created = False

        template.full_clean(validate_unique=False, validate_constraints=False)
        try:
            template.save(using=alias)
        except IntegrityError:
            transaction.set_rollback(True, using=alias)
            conflict = True
        else:
            result = PublicationResult(canonical, template.revision, created)

    if conflict:
        raise PublicationConflict
    return result
