"""Verified-findings regression tests: webhook delivery lifecycle (F6/F7/F8/F26/F33)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules._base import ModelNotFoundError
from src.app.modules.webhooks.delivery import (
    _handle_failure,
    deliver,
    recover_stuck_deliveries,
)
from src.app.modules.webhooks.models import WebhookDeliveryStatus


def _make_cache(held: bool = True) -> AsyncMock:
    cache = AsyncMock()
    cache.client = MagicMock()
    cache.set_nx.return_value = held
    # D8: the lock carries an ownership token and is released with an
    # ownership check, so the release is not a blind delete.
    cache.release_lock_if_owned.return_value = True
    return cache


def _recover_ctx(db: AsyncMock, rows: list) -> tuple[dict, AsyncMock]:
    redis = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute.return_value = result
    return {"redis": redis}, redis


def _ctx_managers(mock_ctx: AsyncMock, mock_bypass: AsyncMock, db: AsyncMock) -> None:
    cm = AsyncMock()
    cm.__aenter__.return_value = db
    cm.__aexit__.return_value = False
    mock_ctx.return_value = cm
    mock_bypass.return_value.__aenter__.return_value = db


async def test_f6_lock_released_when_delivery_missing() -> None:
    """F6: the not-found raise after lock acquire still releases the claim."""
    cache = _make_cache(held=True)
    db = AsyncMock()
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    did = uuid4()

    with patch("src.app.core.cache.client.cache_client", cache):
        with pytest.raises(ModelNotFoundError):
            await deliver(db, did)

    cache.release_lock_if_owned.assert_awaited_once()
    assert cache.release_lock_if_owned.await_args.args[0] == f"webhook:deliver_lock:{did}"


async def test_f6_lock_released_when_endpoint_missing() -> None:
    """F6: missing-endpoint raise releases the claim (previously leaked 60s)."""
    cache = _make_cache(held=True)
    db = AsyncMock()
    delivery = MagicMock()
    delivery.webhook_endpoint = None
    delivery.webhook_endpoint_id = uuid4()
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none.return_value = delivery
    db.execute.return_value = result
    did = uuid4()

    with patch("src.app.core.cache.client.cache_client", cache):
        with pytest.raises(ModelNotFoundError):
            await deliver(db, did)

    cache.release_lock_if_owned.assert_awaited_with(
        f"webhook:deliver_lock:{did}", cache.release_lock_if_owned.await_args.args[1]
    )


async def test_f8_recovery_skips_inflight_delivery() -> None:
    """F8: a row whose lock is held (in-flight) is not re-enqueued."""
    cache = _make_cache(held=False)  # claim fails -> running elsewhere
    db = AsyncMock()
    delivery = MagicMock()
    delivery.id = 7
    delivery.tenant_id = "t1"
    ctx, redis = _recover_ctx(db, [delivery])

    with (
        patch("src.app.modules.webhooks.delivery.get_db_context") as mock_ctx,
        patch("src.app.modules.webhooks.delivery.bypass_rls") as mock_bypass,
        patch("src.app.core.cache.client.cache_client", cache),
    ):
        _ctx_managers(mock_ctx, mock_bypass, db)
        assert await recover_stuck_deliveries(ctx) == 0

    redis.enqueue_job.assert_not_awaited()


async def test_f7_recovery_row_failure_isolated() -> None:
    """F7: one failing row does not discard the batch; per-row commits."""
    cache = _make_cache(held=True)
    db = AsyncMock()
    bad = MagicMock()
    bad.id = 1
    bad.tenant_id = "t1"
    good = MagicMock()
    good.id = 2
    good.tenant_id = "t1"
    ctx, redis = _recover_ctx(db, [bad, good])
    redis.enqueue_job.side_effect = [RuntimeError("lost"), MagicMock()]

    with (
        patch("src.app.modules.webhooks.delivery.get_db_context") as mock_ctx,
        patch("src.app.modules.webhooks.delivery.bypass_rls") as mock_bypass,
        patch("src.app.core.cache.client.cache_client", cache),
    ):
        _ctx_managers(mock_ctx, mock_bypass, db)
        assert await recover_stuck_deliveries(ctx) == 1

    assert db.commit.await_count == 1
    assert db.rollback.await_count == 1
    assert good.next_retry_at is not None


async def test_f26_binary_response_body_recorded() -> None:
    """F26: undecodable-binary bodies decode with replacement, no 500."""
    from src.app.core.utils.circuit_breaker import CircuitState

    cache = _make_cache(held=True)
    db = AsyncMock()
    did = uuid4()
    endpoint = MagicMock()
    endpoint.id = uuid4()
    endpoint.url = "https://example.com/hook"
    endpoint.secret = "enc"
    endpoint.signing_secret = None
    endpoint.old_signing_secret = None
    endpoint.tenant_id = uuid4()
    delivery = MagicMock()
    delivery.id = did
    delivery.tenant_id = endpoint.tenant_id
    delivery.webhook_endpoint = endpoint
    delivery.payload = {"a": 1}
    delivery.attempt_count = 0
    delivery.event_type = "test.event"
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none.return_value = delivery
    db.execute.return_value = result
    db.get.return_value = delivery

    response = MagicMock()
    response.status_code = 200
    response.content = b"\xff\xfe binary \x00\x01" * 500
    http_client = AsyncMock()
    http_client.post.return_value = response
    breaker = MagicMock()
    breaker.state = CircuitState.CLOSED
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    with (
        patch("src.app.core.cache.client.cache_client", cache),
        patch(
            "src.app.core.resilience.registry.get_webhook_breaker",
            return_value=breaker,
        ),
        patch(
            "src.app.modules.webhooks.delivery.validate_webhook_url",
            AsyncMock(return_value="93.184.216.34"),
        ),
        patch("src.app.modules.webhooks.delivery.decrypt_data", return_value="s3cret"),
        patch(
            "src.app.modules.webhooks.delivery.get_webhook_http_client",
            AsyncMock(return_value=http_client),
        ),
    ):
        out = await deliver(db, did)

    assert out.status == WebhookDeliveryStatus.DELIVERED
    assert isinstance(delivery.response_body, str)
    assert len(delivery.response_body) <= 10000
    cache.release_lock_if_owned.assert_awaited_with(
        f"webhook:deliver_lock:{did}", cache.release_lock_if_owned.await_args.args[1]
    )


async def test_f33_subsecond_override_ceils_to_one_second() -> None:
    """F33: override_delay=0.5 must not schedule an immediate retry."""
    delivery = MagicMock()
    delivery.attempt_count = 1
    delivery.id = uuid4()
    delivery.tenant_id = uuid4()
    db = AsyncMock()

    with patch("src.app.modules.webhooks.delivery._spawn_enqueue"):
        await _handle_failure(db, delivery, override_delay=0.5)

    assert delivery.status == WebhookDeliveryStatus.PENDING
    delta = (delivery.next_retry_at - datetime.now(UTC)).total_seconds()
    assert 0 < delta <= 2


async def _half_open_webhook_breaker():
    """A webhook breaker in the virtual HALF_OPEN state."""
    import time

    from src.app.core.utils.circuit_breaker import CircuitBreaker, CircuitState

    breaker = CircuitBreaker(name="webhook-half-open", failure_threshold=1, recovery_timeout=60.0)
    await breaker.record_failure(RuntimeError("endpoint down"))
    breaker._last_failure_time = time.time() - 120
    assert breaker.state == CircuitState.HALF_OPEN
    return breaker


def _delivery_fixture(db: AsyncMock, did) -> MagicMock:
    endpoint = MagicMock()
    endpoint.id = uuid4()
    endpoint.url = "https://example.com/hook"
    endpoint.secret = "enc"
    endpoint.signing_secret = None
    endpoint.old_signing_secret = None
    endpoint.tenant_id = uuid4()
    delivery = MagicMock()
    delivery.id = did
    delivery.tenant_id = endpoint.tenant_id
    delivery.webhook_endpoint = endpoint
    delivery.payload = {"a": 1}
    delivery.attempt_count = 0
    delivery.event_type = "test.event"
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none.return_value = delivery
    db.execute.return_value = result
    db.get.return_value = delivery
    return delivery


async def test_d7_the_half_open_probe_is_claimed_and_released() -> None:
    """D7/D6: exactly ONE delivery probes a recovering endpoint.

    `is_closed` is True during the virtual HALF_OPEN, so every concurrent
    delivery passed the gate and the recovering endpoint took the burst. The
    probe slot must also be released on every exit path.
    """
    cache = _make_cache(held=True)
    db = AsyncMock()
    did = uuid4()
    _delivery_fixture(db, did)
    breaker = await _half_open_webhook_breaker()

    response = MagicMock()
    response.status_code = 200
    response.content = b"ok"
    http_client = AsyncMock()
    http_client.post.return_value = response

    with (
        patch("src.app.core.cache.client.cache_client", cache),
        patch("src.app.core.resilience.registry.get_webhook_breaker", return_value=breaker),
        patch("src.app.modules.webhooks.delivery.validate_webhook_url", AsyncMock(return_value="93.184.216.34")),
        patch("src.app.modules.webhooks.delivery.decrypt_data", return_value="s3cret"),
        patch("src.app.modules.webhooks.delivery.get_webhook_http_client", AsyncMock(return_value=http_client)),
    ):
        await deliver(db, did)

    assert breaker._probe_in_progress is False, "the probe slot must be released"


async def test_d6_the_probe_is_released_on_the_early_return_path() -> None:
    """D6: the SSRF refusal returns before any outcome is recorded.

    The probe slot is released in the `finally`, so a path that never
    records success or failure cannot leave the endpoint wedged.
    """
    from src.app.core.security.url_validator import SSRFProtectionError

    cache = _make_cache(held=True)
    db = AsyncMock()
    did = uuid4()
    _delivery_fixture(db, did)
    breaker = await _half_open_webhook_breaker()

    with (
        patch("src.app.core.cache.client.cache_client", cache),
        patch("src.app.core.resilience.registry.get_webhook_breaker", return_value=breaker),
        patch(
            "src.app.modules.webhooks.delivery.validate_webhook_url",
            AsyncMock(side_effect=SSRFProtectionError("private address")),
        ),
        patch("src.app.modules.webhooks.delivery.decrypt_data", return_value="s3cret"),
    ):
        await deliver(db, did)

    assert breaker._probe_in_progress is False, "the probe slot must be released"
