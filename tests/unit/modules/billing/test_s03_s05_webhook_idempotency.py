"""S-03 / S-04 / S-05 — webhook idempotency state machine.

Three defects in one seam, all about "was this event really handled?":

S-03  ``WebhookEvent`` held only ``event_id`` + ``created_at``, so the row
      ``_db_idempotency_check`` inserts BEFORE the handler runs was
      indistinguishable from the completion marker. A crash between the two left
      a row that read as proof of completion, and with no expiry every later
      Stripe retry was acked ``already_processed`` forever — a paid grant
      dropped with no error anywhere. Fixed with a ``completed_at`` discriminator.

S-04  ``_release_webhook_event_lock`` deleted the Redis key and the row
      UNCONDITIONALLY, so a slow failing delivery erased the terminal marker a
      faster successful one had already written. Fixed with compare-and-delete
      against a per-acquisition lock token.

S-05  A concurrent redelivery was reported ``already_processed`` and acked 200,
      so if the in-flight original failed Stripe never retried. Fixed by
      reporting ``in_flight`` and answering 409.

These are pure-logic tests — every DB session and Redis call is mocked, so they
run without a migrated database.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.billing.webhook import idempotency as idem


def _event_row(*, completed: bool | None) -> MagicMock:
    row = MagicMock()
    row.event_id = "evt_1"
    row.created_at = datetime.now(UTC)
    row.completed_at = datetime.now(UTC) if completed else None
    return row


@contextmanager
def _configured_secret():
    """ingest_webhook refuses to run without a configured Stripe webhook secret."""
    from pydantic import SecretStr

    from src.app.core.config import settings

    original = settings.billing.STRIPE_WEBHOOK_SECRET
    settings.billing.STRIPE_WEBHOOK_SECRET = SecretStr("whsec_test")
    try:
        yield
    finally:
        settings.billing.STRIPE_WEBHOOK_SECRET = original


def _db_ctx(rows: list[MagicMock]) -> MagicMock:
    """An async context manager whose execute() resolves to the given rows."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    db.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestS03ClaimVsCompletion:
    """A CLAIM (completed_at NULL) must never suppress a Stripe retry."""

    @pytest.mark.asyncio
    async def test_abandoned_claim_is_not_processed(self) -> None:
        with patch.object(idem.cache_client, "get", AsyncMock(return_value=None)):
            with patch.object(idem._pkg, "job_bypass_context", return_value=_db_ctx([_event_row(completed=False)])):
                assert await idem._is_event_processed("evt_1") is False

    @pytest.mark.asyncio
    async def test_completed_row_is_processed(self) -> None:
        with patch.object(idem.cache_client, "get", AsyncMock(return_value=None)):
            with patch.object(idem._pkg, "job_bypass_context", return_value=_db_ctx([_event_row(completed=True)])):
                assert await idem._is_event_processed("evt_1") is True

    @pytest.mark.asyncio
    async def test_redis_terminal_marker_still_short_circuits(self) -> None:
        with patch.object(idem.cache_client, "get", AsyncMock(return_value="processed")):
            assert await idem._is_event_processed("evt_1") is True

    @pytest.mark.asyncio
    async def test_db_error_fails_closed(self) -> None:
        with patch.object(idem.cache_client, "get", AsyncMock(return_value=None)):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch.object(idem._pkg, "job_bypass_context", return_value=ctx):
                with pytest.raises(RuntimeError):
                    await idem._is_event_processed("evt_1")


class TestS04CompareAndDelete:
    """A release must only remove state this delivery still owns."""

    @pytest.mark.asyncio
    async def test_release_does_not_blind_delete(self) -> None:
        """The Redis leg goes through compare-and-delete, never a raw DEL."""
        with patch.object(type(idem.cache_client), "client", MagicMock()):
            with patch.object(idem, "_release_if_owned", AsyncMock(return_value=True)) as owned:
                with patch.object(idem._pkg, "job_bypass_context", return_value=_db_ctx([])):
                    await idem._release_webhook_event_lock("evt_1", lock_token="mine")

        owned.assert_awaited_once()
        assert owned.await_args.args[1] == "mine"

    @pytest.mark.asyncio
    async def test_release_with_no_token_skips_redis_leg(self) -> None:
        """The DB-fallback path owns no Redis lock, so nothing may be deleted."""
        with patch.object(type(idem.cache_client), "client", MagicMock()):
            with patch.object(idem, "_release_if_owned", AsyncMock()) as owned:
                with patch.object(idem, "get_redis", MagicMock(return_value=None)):
                    with patch.object(idem._pkg, "job_bypass_context", return_value=_db_ctx([])):
                        await idem._release_webhook_event_lock("evt_1", lock_token=None)

        owned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_delete_only_targets_unstamped_claims(self) -> None:
        """A completed row must survive a later failing delivery's release."""
        from sqlalchemy.dialects import postgresql

        with patch.object(type(idem.cache_client), "client", MagicMock()):
            with patch.object(idem, "_release_if_owned", AsyncMock(return_value=True)):
                with patch.object(idem._pkg, "job_bypass_context", return_value=_db_ctx([])) as ctx:
                    await idem._release_webhook_event_lock("evt_1", lock_token="mine")

        db = ctx.return_value.__aenter__.return_value
        executed = db.execute.await_args_list[0].args[0]
        sql = str(executed.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "completed_at IS NULL" in sql, "release must not delete a completed marker"


class TestS05InFlightIsRetryable:
    @pytest.mark.asyncio
    async def test_in_flight_status_is_reported(self) -> None:
        """A concurrent duplicate must surface as in_flight, not already_processed."""
        from src.app.modules.billing.webhook import dispatch

        event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {}}}

        with patch.object(dispatch._pkg, "_is_event_processed", AsyncMock(return_value=False)):
            with patch.object(dispatch._pkg, "_try_acquire_event_lock", AsyncMock(return_value=(False, None))):
                result = await dispatch.handle_webhook_event(event)

        assert result == {"status": "in_flight"}

    @pytest.mark.asyncio
    async def test_ingest_raises_in_flight_for_retry(self) -> None:
        """ingest_webhook must not flatten in_flight into success."""
        from src.app.modules.billing.webhook.ingest import WebhookInFlight, ingest_webhook

        with _configured_secret():
          with patch(
            "src.app.modules.billing.webhook.ingest.handle_webhook_event",
            AsyncMock(return_value={"status": "in_flight"}),
        ):
            with pytest.raises(WebhookInFlight):
                await ingest_webhook(
                    payload=b"{}",
                    sig_header="t=1,v1=abc",
                    verify_fn=MagicMock(return_value={"id": "evt_1", "type": "x", "data": {"object": {}}}),
                )

    @pytest.mark.asyncio
    async def test_ingest_passes_through_success(self) -> None:
        from src.app.modules.billing.webhook.ingest import ingest_webhook

        with _configured_secret():
          with patch(
            "src.app.modules.billing.webhook.ingest.handle_webhook_event",
            AsyncMock(return_value={"status": "success"}),
        ):
            result = await ingest_webhook(
                payload=b"{}",
                sig_header="t=1,v1=abc",
                verify_fn=MagicMock(return_value={"id": "evt_1", "type": "x", "data": {"object": {}}}),
            )
        assert result == {"status": "success"}
