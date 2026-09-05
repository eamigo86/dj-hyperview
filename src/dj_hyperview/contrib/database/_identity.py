"""Portable byte-exact identities for stored template names."""

import hashlib


def template_name_identity(name: object) -> str:
    """Return a collation-independent identity for one stored name.

    Args:
        name: Value persisted by Django's character field.

    Returns:
        A lowercase SHA-256 hexadecimal identity.
    """
    encoded = str(name).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(encoded).hexdigest()
