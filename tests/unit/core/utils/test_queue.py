"""Tests for the ARQ queue size helper.

Covers src/app/core/utils/queue.py (currently 30% coverage).
"""

from unittest import mock

import pytest

from src.app.core.utils import queue


class TestGetQueueSize:
    """Verify get_queue_size aggregates both ARQ queues."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_pool_none(self) -> None:
        queue.pool = None
        assert await queue.get_queue_size() == 0

    @pytest.mark.asyncio
    async def test_sums_both_queues(self) -> None:
        fake_pool = mock.AsyncMock()
        fake_pipe = mock.AsyncMock()
        fake_pipe.zcard = mock.MagicMock(return_value=fake_pipe)
        fake_pipe.execute = mock.AsyncMock(return_value=[5, 3])
        fake_pool.pipeline = mock.MagicMock(
            return_value=mock.AsyncMock(
                __aenter__=mock.AsyncMock(return_value=fake_pipe),
                __aexit__=mock.AsyncMock(return_value=False),
            )
        )
        queue.pool = fake_pool
        try:
            result = await queue.get_queue_size()
            assert result == 8
            fake_pipe.zcard.assert_any_call("arq:queue")
            fake_pipe.zcard.assert_any_call("arq:queue_low_priority")
        finally:
            queue.pool = None

    @pytest.mark.asyncio
    async def test_handles_none_results(self) -> None:
        fake_pool = mock.AsyncMock()
        fake_pipe = mock.AsyncMock()
        fake_pipe.zcard = mock.MagicMock(return_value=fake_pipe)
        fake_pipe.execute = mock.AsyncMock(return_value=[None, None])
        fake_pool.pipeline = mock.MagicMock(
            return_value=mock.AsyncMock(
                __aenter__=mock.AsyncMock(return_value=fake_pipe),
                __aexit__=mock.AsyncMock(return_value=False),
            )
        )
        queue.pool = fake_pool
        try:
            assert await queue.get_queue_size() == 0
        finally:
            queue.pool = None
