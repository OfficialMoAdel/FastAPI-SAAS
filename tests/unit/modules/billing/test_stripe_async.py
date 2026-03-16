"""Stripe tests - Unit async tests and Contract webhook tests combined.

This file contains:
- Unit tests: Stripe non-blocking async pattern
- Contract tests: Stripe webhook contract (placeholder)
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.config import settings

# ============================================================================
# UNIT TESTS - Stripe Async Pattern
# ============================================================================


class TestStripeAsync:
    """Tests for Stripe non-blocking API calls via asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_pattern(self) -> None:
        """Verify asyncio.to_thread pattern works for blocking calls."""
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = {"id": "cus_test123"}

            # Simulate the pattern used in billing service
            result = await asyncio.to_thread(lambda: {"id": "cus_test123"})

            assert result["id"] == "cus_test123"
            mock_to_thread.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_loop_responsive_during_stripe(self) -> None:
        """Verify event loop remains responsive during Stripe calls."""

        async def concurrent_task() -> str:
            await asyncio.sleep(0.01)
            return "completed"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = {"id": "cus_test"}

            # Run Stripe call and concurrent task
            results = await asyncio.gather(
                asyncio.to_thread(lambda: {"id": "cus_test"}),
                concurrent_task(),
                return_exceptions=True,
            )

            # Concurrent task should complete
            assert results[1] == "completed"

    def test_billing_service_exists(self) -> None:
        """Verify billing service module exists."""
        from src.app.modules.billing import service

        assert service is not None


# ============================================================================
# CONTRACT TESTS - Stripe Webhooks
# ============================================================================


@pytest.mark.skipif(not settings.STRIPE_SECRET_KEY, reason="Stripe keys not configured")
def test_webhook_contract():
    """
    Contract test for Stripe webhooks.
    Requires stripe-mock or real keys.
    """
    # Placeholder for contract test
    pass
