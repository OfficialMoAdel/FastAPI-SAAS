import pytest
from httpx import AsyncClient

from src.app.core.job_monitoring import get_job_monitor


@pytest.mark.asyncio
async def test_job_metrics_endpoint(async_client: AsyncClient, superuser_token_headers):
    # Setup data
    monitor = get_job_monitor()
    await monitor.record_job_start("job1", "test_func")
    await monitor.record_job_success("job1", "test_func", 1.5)

    # Query endpoint
    response = await async_client.get("/api/v1/admin/jobs/metrics", headers=superuser_token_headers)
    assert response.status_code == 200
    data = response.json()

    assert "metrics" in data
    assert "test_func" in data["metrics"]
    metrics = data["metrics"]["test_func"]
    assert metrics["total"] == 1
    assert metrics["success"] == 1
    assert metrics["total_duration"] == 1.5


@pytest.mark.asyncio
async def test_job_status_endpoint(
    async_client: AsyncClient, superuser_token_headers, mock_redis, monkeypatch: pytest.MonkeyPatch
):
    import json
    import time
    from unittest.mock import AsyncMock

    from arq.jobs import Job as ArqJob
    from arq.jobs import JobStatus

    monitor = get_job_monitor()
    await monitor.record_job_start("job2", "test_func_2")

    monkeypatch.setattr("src.app.core.redis_provider.get_arq", lambda: object())
    monkeypatch.setattr(ArqJob, "status", AsyncMock(return_value=JobStatus.queued))

    # K5: the status endpoint reads the arq job definition STRING blob
    # (JSON with enqueue_time in unix ms), not a hash.
    await mock_redis.set(
        "arq:job:job2",
        json.dumps({"job_id": "job2", "function": "test_func_2", "enqueue_time": int(time.time() * 1000)}),
    )

    response = await async_client.get("/api/v1/admin/jobs/status/job2", headers=superuser_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["job_id"] == "job2"


@pytest.mark.asyncio
async def test_dlq_endpoints(async_client: AsyncClient, superuser_token_headers):
    monitor = get_job_monitor()
    await monitor.record_job_failure("job_fail", "fail_func", "Error message")

    # GET DLQ
    response = await async_client.get("/api/v1/admin/jobs/dlq", headers=superuser_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["job_id"] == "job_fail"

    # DELETE DLQ
    response = await async_client.delete("/api/v1/admin/jobs/dlq", headers=superuser_token_headers)
    assert response.status_code == 204

    # Verify empty
    size = await monitor.get_dlq_size()
    assert size == 0


@pytest.mark.asyncio
async def test_summary_endpoint(async_client: AsyncClient, superuser_token_headers):
    monitor = get_job_monitor()
    await monitor.record_job_start("job_sum", "sum_func")
    await monitor.record_job_failure("job_sum", "sum_func", "Error")

    response = await async_client.get("/api/v1/admin/jobs/summary", headers=superuser_token_headers)
    assert response.status_code == 200
    data = response.json()

    # Counts are cumulative across tests if Redis is shared?
    # tests/conftest.py uses one fake_redis instance for session?
    # No, setup_db and others might reset?
    # Actually mock_redis fixture is autouse=True but scope=function default?
    # Let's check fixture scope. `pytest.fixture(autouse=True)` default scope is function.
    # So each test gets fresh FakeRedis.

    assert data["total_failures"] >= 1
    assert "sum_func" in data["active_functions"]
