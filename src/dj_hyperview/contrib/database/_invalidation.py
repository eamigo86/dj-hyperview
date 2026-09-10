"""Schedule database template invalidation at transaction commit."""

from django.db import transaction

from dj_hyperview.cache import invalidate_templates
from dj_hyperview.signals import TemplateInvalidation, template_invalidated
from dj_hyperview.sources import canonicalize_template_name


def _schedule_invalidation(*names: str, using: str) -> None:
    """Schedule canonical template names for post-commit invalidation.

    Args:
        *names: Template names affected by a database mutation.
        using: Database alias whose transaction owns the mutation.

    Raises:
        InvalidTemplateName: If any template name is unsafe.
        ConnectionDoesNotExist: If the database alias is not configured.
        SourceUnavailable: If immediate or committed cache invalidation fails.
    """
    canonical = tuple(dict.fromkeys(canonicalize_template_name(name) for name in names))
    if not canonical:
        return
    event = TemplateInvalidation(names=frozenset(canonical), using=using)

    def invalidate() -> None:
        from .models import HyperviewTemplate

        try:
            invalidate_templates(*canonical)
        finally:
            template_invalidated.send_robust(sender=HyperviewTemplate, event=event)

    transaction.on_commit(invalidate, using=using, robust=False)
