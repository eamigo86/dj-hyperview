"""Validated publication services for optional database templates."""

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import DatabaseError, IntegrityError, router, transaction
from django.db.utils import Error

from dj_hyperview.exceptions import HyperviewError, SourceUnavailable
from dj_hyperview.sources import canonicalize_template_name

from ._config import _database_alias_is_configured
from ._identity import template_name_identity

SOURCE = "database"

__all__ = [
    "PublicationConflict",
    "PublicationResult",
    "delete_template",
    "publish_template",
    "rename_template",
]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Describe one template mutation persisted in the current transaction.

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
        """Initialize a redacted publication conflict."""
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


def _mutation_target(using: str | None) -> tuple[type[Any], str]:
    if using is not None and not _database_alias_is_configured(using):
        raise SourceUnavailable(SOURCE, "alias unavailable")
    model = _template_model()
    if model is None:
        raise SourceUnavailable(SOURCE, "app unavailable")
    return model, _database_alias(model, using)


def _validate_expected_revision(expected_revision: int | None) -> None:
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer or None")


def _proof_exists(queryset: Any) -> bool | None:
    try:
        return queryset.exists()
    except Error:
        return None


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
        Exception: If an unclassified database write error is reraised.
    """
    canonical = canonicalize_template_name(name)
    identity = template_name_identity(canonical)
    _validate_expected_revision(expected_revision)
    model, alias = _mutation_target(using)
    conflict = False
    result: PublicationResult

    with transaction.atomic(using=alias):
        manager = model._default_manager.using(alias)
        try:
            template = manager.select_for_update().get(name_identity=identity)
        except model.DoesNotExist:
            template = None
        if template is not None and template.name != canonical:
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
        if created:
            try:
                with transaction.atomic(using=alias):
                    template.save(using=alias)
            except IntegrityError:
                conflict = manager.filter(name_identity=identity).exists()
                if not conflict:
                    raise
        else:
            template.save(using=alias)
        if not conflict:
            result = PublicationResult(canonical, template.revision, created)

    if conflict:
        raise PublicationConflict
    return result


def rename_template(
    current_name: str,
    new_name: str,
    *,
    content: str | None = None,
    active: bool | None = None,
    expected_revision: int | None = None,
    using: str | None = None,
) -> PublicationResult:
    """Rename one validated database template atomically.

    Args:
        current_name: Stored name identifying the current template.
        new_name: Canonical name to persist after the mutation.
        content: Replacement source, or None to preserve it.
        active: Replacement activity state, or None to preserve it.
        expected_revision: Existing revision required for the mutation.
        using: Explicit database alias, or None to use Django write routing.

    Returns:
        Immutable metadata for the persisted mutation.

    Raises:
        InvalidTemplateName: If the replacement name is unsafe or noncanonical.
        ValueError: If expected_revision is not a positive integer or None.
        ValidationError: If model field validation rejects the mutation.
        PublicationConflict: If the source is missing, the revision is stale,
            or another row owns the target name.
        SourceUnavailable: If the optional app or database alias is unavailable.
        DatabaseError: If database access fails.
        Exception: If an unclassified database write error is reraised.
    """
    current = current_name
    target = canonicalize_template_name(new_name)
    current_identity = template_name_identity(current)
    target_identity = template_name_identity(target)
    _validate_expected_revision(expected_revision)
    model, alias = _mutation_target(using)
    conflict = False
    result: PublicationResult

    with transaction.atomic(using=alias):
        manager = model._default_manager.using(alias)
        try:
            template = manager.select_for_update().get(name_identity=current_identity)
        except model.DoesNotExist:
            template = None
        if template is not None and template.name != current:
            template = None
        if template is None:
            raise PublicationConflict
        if expected_revision is not None and template.revision != expected_revision:
            raise PublicationConflict
        template.name = target
        if content is not None:
            template.content = content
        if active is not None:
            template.active = active
        template.revision += 1
        template.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic(using=alias):
                template.save(using=alias, force_update=True)
        except IntegrityError:
            competing_target = _proof_exists(
                manager.filter(name_identity=target_identity).exclude(pk=template.pk)
            )
            if competing_target is None or not competing_target:
                raise
            conflict = True
        except DatabaseError:
            source_exists = _proof_exists(
                manager.filter(pk=template.pk, name_identity=current_identity)
            )
            if source_exists is None or source_exists:
                raise
            conflict = True
        if not conflict:
            result = PublicationResult(target, template.revision, False)

    if conflict:
        raise PublicationConflict
    return result


def delete_template(
    name: str,
    *,
    expected_revision: int | None = None,
    using: str | None = None,
) -> bool:
    """Delete one database template atomically.

    Args:
        name: Canonical name identifying the template.
        expected_revision: Existing revision required for deletion.
        using: Explicit database alias, or None to use Django write routing.

    Returns:
        True when a row was deleted, otherwise False for an unguarded miss.

    Raises:
        InvalidTemplateName: If the template name is unsafe or noncanonical.
        ValueError: If expected_revision is not a positive integer or None.
        PublicationConflict: If a guarded template is missing or stale, or a
            locked row disappears before deletion.
        SourceUnavailable: If the optional app or database alias is unavailable.
        DatabaseError: If database access fails.
    """
    canonical = canonicalize_template_name(name)
    identity = template_name_identity(canonical)
    _validate_expected_revision(expected_revision)
    model, alias = _mutation_target(using)
    with transaction.atomic(using=alias):
        manager = model._default_manager.using(alias)
        try:
            template = manager.select_for_update().get(name_identity=identity)
        except model.DoesNotExist:
            template = None
        if template is not None and template.name != canonical:
            template = None
        if template is None:
            if expected_revision is None:
                return False
            raise PublicationConflict
        if expected_revision is not None and template.revision != expected_revision:
            raise PublicationConflict
        deleted, _ = template.delete(using=alias)
        if deleted == 0:
            raise PublicationConflict
    return True
