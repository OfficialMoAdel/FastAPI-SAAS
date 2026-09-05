"""Unit tests for ARQ wire protocol adapter (Candidate 5)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.events.arq_adapter import (
    JOB_EXPIRES_MS,
    deserialize_job,
    enqueue_batch_jobs,
    get_arq_pool_kwargs,
    get_queue_name,
    serialize_job,
)
from src.app.core.events.priority import EventPriority


def test_serialize_and_deserialize_job_parity():
    """Verify serializer parity across web and worker (K1 fix)."""
    payload = {"event": "user.login", "user_id": 123, "active": True}
    serialized = serialize_job(payload)
    assert isinstance(serialized, bytes)

    deserialized = deserialize_job(serialized)
    assert deserialized == payload


def test_get_queue_name_priorities():
    """Verify single and batch jobs use consistent queue naming."""
    normal_q = get_queue_name(EventPriority.NORMAL)
    assert normal_q == "arq:queue" or "queue" in normal_q

    low_q = get_queue_name(EventPriority.LOW)
    assert low_q.endswith("_low_priority")


def test_get_arq_pool_kwargs():
    kwargs = get_arq_pool_kwargs()
    assert kwargs["job_serializer"] is serialize_job
    assert kwargs["job_deserializer"] is deserialize_job


@pytest.mark.asyncio
async def test_enqueue_batch_jobs_pipeline():
    """Verify batch job pipeline writes psetex with 24h expiration and zadd with queue."""
    mock_queue = MagicMock()
    mock_pipe = AsyncMock()
    mock_queue.pipeline.return_value.__aenter__.return_value = mock_pipe
    mock_queue.job_serializer = serialize_job

    async def dummy_handler(data):
        pass

    await enqueue_batch_jobs(
        mock_queue,
        event_name="order.placed",
        handlers=[dummy_handler],
        data={"order_id": "ord_1"},
        priority=EventPriority.NORMAL,
    )

    mock_pipe.psetex.assert_called_once()
    mock_pipe.zadd.assert_called_once()
    assert mock_pipe.psetex.call_args[0][1] == JOB_EXPIRES_MS
    assert mock_pipe.execute.await_count == 1
