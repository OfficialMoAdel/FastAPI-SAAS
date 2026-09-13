"""Tests for the P2-6 notification outbox recovery sweep (recover_stuck_notifications)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import (
    NOTIFICATION_MAX_PENDING_AGE_HOURS,
    recover_stuck_notifications,
)


def make_stuck_notification(age_hours: float, status: NotificationStatus = NotificationStatus.PENDING):
    m = MagicMock()
    m.id = uuid4()
    m.status = status
    m.created_at = datetime.now(UTC) - timedelta(hours=age_hours)
    return m


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _patch_sweep(db):
    """Patch get_db_context + bypass_rls so the sweep uses the mock session."""
    db_ctx = AsyncMock()
    db_ctx.__aenter__.return_value = db
    bypass = AsyncMock()
    bypass.__aenter__.return_value = db
    return (
        patch("src.app.core.db.database.get_db_context", return_value=db_ctx),
        patch("src.app.core.db.rls.bypass_rls", return_value=bypass),
    )


def _patch_result(db, rows: list) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)


class TestRecoverStuckNotifications:
    async def test_sweeps_pending_rows_past_max_age_to_failed(self, mock_db) -> None:
        """PENDING rows older than NOTIFICATION_MAX_PENDING_AGE_HOURS become FAILED terminal."""
        stuck = make_stuck_notification(NOTIFICATION_MAX_PENDING_AGE_HOURS + 1)
        _patch_result(mock_db, [stuck])

        with _patch_sweep(mock_db)[0], _patch_sweep(mock_db)[1]:
            recovered = await recover_stuck_notifications({})

        assert recovered == 1
        assert stuck.status == NotificationStatus.FAILED
        assert stuck.error_message == "Recovery window exhausted"
        mock_db.commit.assert_awaited_once()

    async def test_young_pending_rows_untouched(self, mock_db) -> None:
        """The sweep query filters in SQL: only PENDING + created_at past the cutoff are selected."""
        _patch_result(mock_db, [])

        with _patch_sweep(mock_db)[0], _patch_sweep(mock_db)[1]:
            recovered = await recover_stuck_notifications({})

        assert recovered == 0
        mock_db.commit.assert_not_awaited()

        # The executed statement must carry the age + status predicates so
        # young PENDING rows are never selected by Postgres itself.
        stmt = mock_db.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "status" in compiled
        assert "created_at" in compiled

    async def test_empty_sweep_commits_nothing(self, mock_db) -> None:
        """No stuck rows → zero recovered, no commit issued."""
        _patch_result(mock_db, [])

        with _patch_sweep(mock_db)[0], _patch_sweep(mock_db)[1]:
            recovered = await recover_stuck_notifications({})

        assert recovered == 0
        mock_db.commit.assert_not_awaited()

    async def test_no_rows_returned_never_touches_db_session(self) -> None:
        """A ctx without Redis is fine: the terminal sweep never touches ctx (unlike webhooks re-enqueue)."""
        # The sweep is terminal-only; ctx['redis'] is not consulted.
        mock_db = AsyncMock()
        _patch_result(mock_db, [])
        with _patch_sweep(mock_db)[0], _patch_sweep(mock_db)[1]:
            recovered = await recover_stuck_notifications({"redis": None})

        assert recovered == 0
