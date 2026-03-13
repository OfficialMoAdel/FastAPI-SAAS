from datetime import UTC, datetime

import pytest

from src.app.core.job_monitoring import JobMonitoringService
from src.app.core.utils import cache


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
    start_time = datetime.now(UTC).timestamp()
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


# =============================================================================
# E2E Tests: Job Monitoring
# =============================================================================


@pytest.mark.asyncio
async def test_job_monitoring_e2e_flow(e2e_client, auth_headers):
    """
    E2E Test for Job Monitoring.
    """
    from unittest.mock import patch
    from uuid import uuid4

    from src.app.core.db.database import async_get_db
    from src.app.core.worker.functions import deliver_webhook_task, sample_background_task
    from src.app.main import app
    from tests.conftest import TestingSessionLocal, override_get_db

    # Override dependency to use test DB
    app.dependency_overrides[async_get_db] = override_get_db

    from contextlib import nullcontext

    with nullcontext():
        # Patch local_session in functions.py so workers use test DB too
        with patch("src.app.core.db.database.local_session", TestingSessionLocal):
            # Now call workers

            # 1. Execute success task
            job_id = f"e2e-job-{uuid4()}"
            ctx = {"job_id": job_id, "job_try": 1, "enqueue_time": 1234567890}

            result = await sample_background_task(ctx, "e2e_test")  # type: ignore[arg-type]
            assert result == "Task e2e_test is complete!"

            # 2. Verify metrics via API
            response = await e2e_client.get("/api/v1/admin/jobs/metrics", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()

            assert "metrics" in data
            assert "sample_background_task" in data["metrics"]
            metrics = data["metrics"]["sample_background_task"]
            assert metrics["total"] >= 1
            assert metrics["success"] >= 1

            # 3. Execute failing task
            fail_job_id = f"e2e-fail-{uuid4()}"
            fail_ctx = {"job_id": fail_job_id, "job_try": 1}

            try:
                await deliver_webhook_task(fail_ctx, "invalid-uuid")  # type: ignore[arg-type]
            except ValueError:
                pass
            except Exception:
                pass

            # 4. Verify DLQ
            response = await e2e_client.get("/api/v1/admin/jobs/dlq", headers=auth_headers)
            assert response.status_code == 200
            dlq = response.json()

            found = False
            for item in dlq:
                if item["job_id"] == fail_job_id:
                    found = True
                    assert item["function"] == "deliver_webhook_task"
                    break

            assert found

    # Cleanup dependency overrides
    app.dependency_overrides = {}
