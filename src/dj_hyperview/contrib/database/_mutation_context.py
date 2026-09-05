"""Internal context shared by controlled ORM mutation paths."""

from contextvars import ContextVar
from typing import Any

batch_delete_primary_keys: ContextVar[frozenset[Any] | None] = ContextVar(
    "dj_hyperview_batch_delete_primary_keys", default=None
)
