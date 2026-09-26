"""The one acceptance sequence both doors into the email pipeline run.

Card 11: the direct door (``NotificationService.send_email``) and the event
door (``handle_notification_request_event``) had the same three ordered steps
written out twice. They now share ``NotificationService._accept_notification``;
these tests pin the properties that made the duplication dangerous:

* both doors go through the shared sequence (not a re-inlined copy),
* both doors keep their own return shape (Notification | None vs nothing),
* both doors emit the rate-limit refusal warning with the same fields.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import (
    NotificationService,
    handle_notification_request_event,
)
from src.app.shared.exceptions import RateLimitExceededError

LIMIT_REASON = "100 emails per minute"


def _refusal_warnings(mock_logger: MagicMock) -> list[tuple[str, dict[str, object]]]:
    """(event, kwargs) for every notification_rate_limit_exceeded warning logged."""
    return [
        (call.args[0], call.kwargs)
        for call in mock_logger.warning.call_args_list
        if call.args[0] == "notification_rate_limit_exceeded"
    ]


def _service_and_db() -> tuple[NotificationService, AsyncMock]:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return NotificationService(db=db), db


def _event_door_session() -> tuple[AsyncMock, MagicMock]:
    """Mock db + an async-context session factory for the event door."""
    db = AsyncMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = db
    return db, MagicMock(return_value=session_ctx)


class TestBothDoorsShareOneSequence:
    @pytest.mark.asyncio
    async def test_both_doors_delegate_to_the_same_method(self) -> None:
        """Both doors call _accept_notification — the sequence is not re-inlined."""
        service, _ = _service_and_db()
        event_db, session = _event_door_session()
        accepted = MagicMock(id=uuid4())
        tenant_id = uuid4()

        with patch.object(
            NotificationService, "_accept_notification", new_callable=AsyncMock
        ) as mock_sequence:
            mock_sequence.return_value = accepted

            result = await service.send_email(
                tenant_id, "user@example.com", "welcome", {"a": 1}, user_id=7
            )
            assert result is accepted, "direct door returns the sequence's outcome"

            with patch("src.app.core.db.database.local_session", session):
                await handle_notification_request_event({
                    "to_email": "user@example.com",
                    "template_name": "welcome",
                    "context": {"a": 1},
                    "user_id": 7,
                })

            assert mock_sequence.await_count == 2, "both doors must run the shared sequence"
            assert event_db.add.call_count == 0, "the sequence body was stubbed; the door added nothing"

    @pytest.mark.asyncio
    async def test_direct_door_still_returns_none_when_skipped(self) -> None:
        """The direct door's None-means-skipped return shape is preserved."""
        service, db = _service_and_db()

        with (
            patch("src.app.modules.notifications.service.consume", new_callable=AsyncMock) as mock_consume,
            patch.object(service, "should_send_notification", new_callable=AsyncMock) as mock_prefs,
        ):
            mock_consume.return_value = (False, 0, 0, False)
            mock_prefs.return_value = False

            result = await service.send_email(uuid4(), "user@example.com", "welcome", {}, user_id=7)

        assert result is None
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_door_returns_nothing_even_when_accepted(self) -> None:
        """The event door's return shape (always None) is preserved."""
        _, session = _event_door_session()
        with (
            patch("src.app.core.db.database.local_session", session),
            patch.object(
                NotificationService, "_accept_notification", new_callable=AsyncMock
            ) as mock_sequence,
        ):
            mock_sequence.return_value = MagicMock()
            result = await handle_notification_request_event({
                "to_email": "user@example.com",
                "template_name": "welcome",
            })
        assert result is None


class TestRateLimitRefusalIsIdenticalOnBothDoors:
    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.consume", new_callable=AsyncMock)
    async def test_direct_door_writes_audit_row_and_warns(self, mock_consume) -> None:
        """Direct door: FAILED audit row + RateLimitExceededError + the warning."""
        mock_consume.return_value = (True, 0, 0, False)
        service, db = _service_and_db()
        tenant_id = uuid4()

        with patch("src.app.modules.notifications.service.logger") as mock_logger:
            with pytest.raises(RateLimitExceededError):
                await service.send_email(tenant_id, "user@example.com", "welcome", {})
            warnings = _refusal_warnings(mock_logger)

        assert warnings == [(
            "notification_rate_limit_exceeded",
            {"tenant_id": str(tenant_id), "reason": LIMIT_REASON},
        )]

        added = db.add.call_args.args[0]
        assert added.status == NotificationStatus.FAILED
        assert added.subject == "Rate limited: welcome"
        assert added.error_message == f"Rate limit exceeded: {LIMIT_REASON}"

    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.consume", new_callable=AsyncMock)
    async def test_event_door_writes_audit_row_and_warns(self, mock_consume) -> None:
        """Event door: the same row, the same exception, the same warning."""
        mock_consume.return_value = (True, 0, 0, False)
        db, session = _event_door_session()
        tenant_id = uuid4()
        warnings: list[tuple[str, dict[str, object]]] = []

        with (
            patch("src.app.core.db.database.local_session", session),
            patch("src.app.modules.notifications.service.logger") as mock_logger,
        ):
            with pytest.raises(RateLimitExceededError):
                await handle_notification_request_event({
                    "to_email": "user@example.com",
                    "template_name": "welcome",
                    "tenant_id": str(tenant_id),
                })
            warnings = _refusal_warnings(mock_logger)

        assert warnings == [(
            "notification_rate_limit_exceeded",
            {"tenant_id": str(tenant_id), "reason": LIMIT_REASON},
        )]

        added = db.add.call_args.args[0]
        assert added.status == NotificationStatus.FAILED
        assert added.subject == "Rate limited: welcome"
        assert added.error_message == f"Rate limit exceeded: {LIMIT_REASON}"

    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.consume", new_callable=AsyncMock)
    async def test_both_doors_report_the_refusal_identically(self, mock_consume) -> None:
        """One door cannot log a different refusal than the other."""
        mock_consume.return_value = (True, 0, 0, False)
        tenant_id = uuid4()
        service, _ = _service_and_db()
        _, session = _event_door_session()

        with patch("src.app.modules.notifications.service.logger") as mock_logger:
            with pytest.raises(RateLimitExceededError):
                await service.send_email(tenant_id, "user@example.com", "welcome", {})
            direct_warnings = _refusal_warnings(mock_logger)

        with (
            patch("src.app.core.db.database.local_session", session),
            patch("src.app.modules.notifications.service.logger") as mock_logger,
        ):
            with pytest.raises(RateLimitExceededError):
                await handle_notification_request_event({
                    "to_email": "user@example.com",
                    "template_name": "welcome",
                    "tenant_id": str(tenant_id),
                })
            event_warnings = _refusal_warnings(mock_logger)

        assert direct_warnings == event_warnings
        assert direct_warnings, "both doors must actually warn"
