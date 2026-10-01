"""Unit tests for worker RLS-bypass context hygiene (P2 hygiene slice).

Covers the record-then-proceed contract of job_bypass_context:
- an invalid reason fails BEFORE any audit await (caller-bug surfaced early);
- a BEST_EFFORT audit delivery failure never blocks the job (logged, swallowed).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.worker import rls_context


@pytest.fixture(autouse=True)
def _fake_db_seams():
    """Replace the DB session + RLS bypass with no-op async CMs."""

    @asynccontextmanager
    async def fake_session() -> AsyncGenerator[object]:
        yield object()

    @asynccontextmanager
    async def fake_bypass(session: object, reason: str) -> AsyncGenerator[object]:
        yield session

    with (
        patch.object(rls_context, "local_session", fake_session),
        patch("src.app.core.db.rls.bypass_rls", fake_bypass),
    ):
        yield


@pytest.mark.asyncio
async def test_invalid_reason_fails_before_audit_await():
    """A validation failure is the caller's bug — it must surface before the
    best-effort audit await, never mid-context."""
    with patch("src.app.modules.audit_logs.audit", new_callable=AsyncMock) as mock_audit:
        with pytest.raises(ValueError, match="Reason is required"):
            async with rls_context.job_bypass_context("   "):
                pass  # pragma: no cover - context never entered
    mock_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_delivery_failure_does_not_block_job():
    """BEST_EFFORT delivery failure (validation aside) is swallowed and the
    job context still opens with its session."""
    with (
        patch("src.app.modules.audit_logs.audit", new_callable=AsyncMock, side_effect=RuntimeError("redis down")),
        patch("src.app.core.worker.rls_context.logger") as mock_logger,
    ):
        async with rls_context.job_bypass_context("system flush") as session:
            assert session is not None
    # The lost trail must be loud (error-level audit_bypass_unaudited event).
    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.args[0] == "audit_bypass_unaudited"


@pytest.mark.asyncio
async def test_clean_audit_delivery_yields_session():
    """The happy path: audit recorded, bypass session yielded."""
    with patch("src.app.modules.audit_logs.audit", new_callable=AsyncMock) as mock_audit:
        async with rls_context.job_bypass_context("system flush") as session:
            assert session is not None
    mock_audit.assert_awaited_once()
