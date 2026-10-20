"""Fixtures shared by the test modules."""

from collections.abc import Iterator

import pytest


@pytest.fixture
def cold_schema_caches() -> Iterator[None]:
    """Start with no compiled registry or catalog and leave none behind.

    The compiled results outlive ``override_settings``, because their keys hold every
    input, so a test that counts compilations needs an empty cache, and a test that
    compiles through a patched seam must not leave that result for the next test.
    """
    from dj_hyperview.schema import (
        _active_catalog,
        _cached_registry,
        _cached_static_validation_catalog,
    )

    def clear() -> None:
        _cached_registry.cache_clear()
        _cached_static_validation_catalog.cache_clear()
        _active_catalog.cache_clear()

    clear()
    yield
    clear()
