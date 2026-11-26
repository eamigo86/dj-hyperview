"""Fixtures shared by the test modules."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.response_documents import EXTRA_XSD


@pytest.fixture
def extra_schema(tmp_path: Path) -> Path:
    """Write the extra schema of the response tests and return its path.

    Args:
        tmp_path: Temporary directory that holds the schema.

    Returns:
        Path of the written extra schema file.
    """
    path = tmp_path / "extra.xsd"
    path.write_text(EXTRA_XSD, encoding="utf-8")
    return path


@pytest.fixture
def cold_schema_caches() -> Iterator[None]:
    """Start with no compiled registry or catalog and leave none behind.

    Compiled results outlive override_settings, so a test that counts compilations
    needs an empty cache, and a test that compiles through a patched seam must not
    leave that result for the next test.

    Yields:
        Nothing; the compiled registry and catalog caches are empty during the test.
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
