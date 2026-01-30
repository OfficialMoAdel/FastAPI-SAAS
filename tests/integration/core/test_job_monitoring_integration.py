import asyncio
import pytest
from src.app.core.job_monitoring import JobMonitoringService
from src.app.core.utils import cache
from arq.connections import ArqRedis
from unittest.mock import MagicMock
from datetime import datetime, timezone


# Use the existing integration test setup style
@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_monitoring_flow():
    """
    Test the full job monitoring flow with real Redis.
    1. Record job start
    2. Record job success
    3. Verify metrics
    4. Record job failure
    5. Verify DLQ
    """
    # Ensure Redis is available (patched or real)
    redis_client = cache.client
    if not redis_client:
        pytest.skip("Redis client not initialized")

    # Clean up previous test data
    await redis_client.delete("arq:stats:test_job")
    await redis_client.delete("arq:dlq")

    service = JobMonitoringService()
    job_id = "test_job_123"
    function_name = "test_job"

    # 1. Record Start
    await service.record_job_start(job_id, function_name)

    # Verify 'total' count incremented
    all_stats = await service.get_job_metrics()
    stats = all_stats.get(function_name, {})
    assert stats.get("total", 0) == 1
    assert stats.get("success", 0) == 0
    assert stats.get("failed", 0) == 0

    # 2. Record Success
    start_time = datetime.now(timezone.utc).timestamp()
    await service.record_job_success(job_id, function_name, start_time)

    # Verify 'success' count
    all_stats = await service.get_job_metrics()
    stats = all_stats.get(function_name, {})
    assert stats.get("total", 0) == 1
    assert stats.get("success", 0) == 1
    assert stats.get("failed", 0) == 0

    # 3. Record Failure
    fail_job_id = "test_job_fail_456"
    await service.record_job_start(fail_job_id, function_name)
    await service.record_job_failure(fail_job_id, function_name, str(ValueError("Something went wrong")))

    # Verify 'failed' count
    all_stats = await service.get_job_metrics()
    stats = all_stats.get(function_name, {})
    assert stats.get("total", 0) == 2
    assert stats.get("success", 0) == 1
    assert stats.get("failed", 0) == 1

    # 4. Verify DLQ
    dlq = await service.get_dead_letter_queue()
    assert len(dlq) == 1
    assert dlq[0]["job_id"] == fail_job_id
    assert dlq[0]["function"] == function_name
    assert dlq[0]["error"] == "Something went wrong"

    # Cleanup
    await redis_client.delete(f"arq:stats:{function_name}")
    await redis_client.delete("arq:dlq")
