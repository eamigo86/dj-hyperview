"""Transactional invalidation scheduling integration tests."""

from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.db import transaction
from django.db.utils import ConnectionDoesNotExist

from dj_hyperview.contrib.database._invalidation import _schedule_invalidation
from dj_hyperview.exceptions import InvalidTemplateName, SourceUnavailable

pytestmark = pytest.mark.django_db(
    transaction=True,
    databases=("default", "replica"),
)


def test_scheduler_invalidates_immediately_in_autocommit() -> None:
    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        _schedule_invalidation("screen.xml", using="default")

    invalidate.assert_called_once_with("screen.xml")


def test_scheduler_waits_for_outer_commit_and_skips_rollback() -> None:
    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic(using="default"):
            _schedule_invalidation("committed.xml", using="default")
            invalidate.assert_not_called()
        invalidate.assert_called_once_with("committed.xml")
        invalidate.reset_mock()

        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic(using="default"):
                _schedule_invalidation("rolled-back.xml", using="default")
                raise RuntimeError("rollback")

    invalidate.assert_not_called()


def test_nested_savepoint_rollback_discards_only_nested_callback() -> None:
    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with transaction.atomic(using="default"):
            with pytest.raises(RuntimeError, match="savepoint"):
                with transaction.atomic(using="default"):
                    _schedule_invalidation("nested.xml", using="default")
                    raise RuntimeError("savepoint")
            _schedule_invalidation("outer.xml", using="default")
            invalidate.assert_not_called()

    invalidate.assert_called_once_with("outer.xml")


def test_scheduler_uses_the_selected_database_connection() -> None:
    events: list[tuple[str, ...]] = []

    def record(*names: str) -> None:
        events.append(names)

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates",
        side_effect=record,
    ):
        with transaction.atomic(using="default"):
            with transaction.atomic(using="replica"):
                _schedule_invalidation("replica.xml", using="replica")
                _schedule_invalidation("default.xml", using="default")
            assert events == [("replica.xml",)]
        assert events == [("replica.xml",), ("default.xml",)]


def test_scheduler_validates_all_names_before_registering() -> None:
    with patch(
        "dj_hyperview.contrib.database._invalidation.transaction.on_commit"
    ) as on_commit:
        with pytest.raises(InvalidTemplateName, match="Invalid template name"):
            _schedule_invalidation("valid.xml", "../private.xml", using="default")

    on_commit.assert_not_called()


def test_scheduler_deduplicates_order_into_immutable_callback_capture() -> None:
    callbacks: list[Callable[[], None]] = []

    def register(callback: Callable[[], None], *, using: str, robust: bool) -> None:
        assert (using, robust) == ("default", False)
        callbacks.append(callback)

    names = ["second.xml", "first.xml", "second.xml"]
    with (
        patch(
            "dj_hyperview.contrib.database._invalidation.transaction.on_commit",
            side_effect=register,
        ),
        patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates"
        ) as invalidate,
    ):
        _schedule_invalidation(*names, using="default")
        names[:] = ["changed.xml"]
        callbacks[0]()

    assert len(callbacks) == 1
    invalidate.assert_called_once_with("second.xml", "first.xml")


def test_scheduler_with_no_names_has_no_transaction_or_cache_effect() -> None:
    with (
        patch(
            "dj_hyperview.contrib.database._invalidation.transaction.on_commit"
        ) as on_commit,
        patch(
            "dj_hyperview.contrib.database._invalidation.invalidate_templates"
        ) as invalidate,
    ):
        _schedule_invalidation(using="default")

    on_commit.assert_not_called()
    invalidate.assert_not_called()


def test_scheduler_preserves_django_invalid_alias_behavior() -> None:
    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates"
    ) as invalidate:
        with pytest.raises(ConnectionDoesNotExist):
            _schedule_invalidation("screen.xml", using="missing")

    invalidate.assert_not_called()


def test_cache_failure_is_observable_after_successful_commit() -> None:
    callback_autocommit: list[bool] = []

    def fail_after_commit(*names: str) -> None:
        assert names == ("screen.xml",)
        callback_autocommit.append(transaction.get_autocommit(using="default"))
        raise SourceUnavailable("cache:test", "backend failure")

    with patch(
        "dj_hyperview.contrib.database._invalidation.invalidate_templates",
        side_effect=fail_after_commit,
    ):
        with pytest.raises(SourceUnavailable, match="backend failure"):
            with transaction.atomic(using="default"):
                _schedule_invalidation("screen.xml", using="default")
                assert callback_autocommit == []

    assert callback_autocommit == [True]
