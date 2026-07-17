"""Unit tests for webhook recovery job (recover_stuck_deliveries)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.app.modules.webhooks.recovery import recover_stuck_deliveries


async def test_recover_no_redis_returns_zero() -> None:
    ctx: dict = {}
    assert await recover_stuck_deliveries(ctx) == 0


async def test_recover_no_stuck_deliveries() -> None:
    redis = AsyncMock()
    delivery = MagicMock()
    delivery.id = 1
    delivery.tenant_id = "t1"
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result

    ctx = {"redis": redis}
    with (
        patch("src.app.modules.webhooks.recovery.get_db_context") as mock_ctx,
        patch("src.app.modules.webhooks.recovery.bypass_rls") as mock_bypass,
    ):
        cm = AsyncMock()
        cm.__aenter__.return_value = db
        cm.__aexit__.return_value = False
        mock_ctx.return_value = cm
        mock_bypass.return_value.__aenter__.return_value = db
        assert await recover_stuck_deliveries(ctx) == 0
    redis.enqueue_job.assert_not_awaited()


async def test_recover_reenqueues_stuck_deliveries() -> None:
    redis = AsyncMock()
    delivery = MagicMock()
    delivery.id = 42
    delivery.tenant_id = "tenant-xyz"
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [delivery]
    db.execute.return_value = result

    ctx = {"redis": redis}
    with (
        patch("src.app.modules.webhooks.recovery.get_db_context") as mock_ctx,
        patch("src.app.modules.webhooks.recovery.bypass_rls") as mock_bypass,
    ):
        cm = AsyncMock()
        cm.__aenter__.return_value = db
        cm.__aexit__.return_value = False
        mock_ctx.return_value = cm
        mock_bypass.return_value.__aenter__.return_value = db
        recovered = await recover_stuck_deliveries(ctx)

    assert recovered == 1
    redis.enqueue_job.assert_awaited_once_with(
        "deliver_webhook_task",
        delivery_id="42",
        tenant_id="tenant-xyz",
        _job_id="recover_webhook_42",
    )
    db.commit.assert_awaited_once()
