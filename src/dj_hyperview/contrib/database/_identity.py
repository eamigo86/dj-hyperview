"""Portable byte-exact identities for stored template names."""

import hashlib
from typing import Any

from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.sources import canonicalize_template_name


def template_name_identity(name: object) -> str:
    """Return a collation-independent identity for one stored name.

    Args:
        name: Value persisted by Django's character field.

    Returns:
        A lowercase SHA-256 hexadecimal identity.
    """
    encoded = str(name).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_name_or_none(value: object) -> str | None:
    try:
        return canonicalize_template_name(value)
    except InvalidTemplateName:
        return None


def _assign_template_name_identity(
    instance: Any, update_fields: frozenset[str] | set[str] | None = None
) -> None:
    if instance._state.adding or update_fields is None or "name" in update_fields:
        instance.name_identity = template_name_identity(instance.name)
