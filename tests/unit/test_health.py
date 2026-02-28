from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient

from src.app.core.health import HealthResult


@pytest.fixture
def mock_health_checks():
    # check_database_health and check_redis_health are imported at top-level in router,
    # so we must patch them WHERE THEY ARE USED (in the router module).
    # check_redis_queue_health is imported inside the function, so we must patch it at SOURCE.
    with (
        patch("src.app.modules.health.router.check_database_health", new_callable=AsyncMock) as db_mock,
        patch("src.app.modules.health.router.check_redis_health", new_callable=AsyncMock) as redis_mock,
        patch("src.app.core.health.check_redis_queue_health", new_callable=AsyncMock) as queue_mock,
    ):
        yield db_mock, redis_mock, queue_mock


@pytest.fixture
def mock_event_bus():
    from unittest.mock import patch, PropertyMock
    from src.app.core.events import EventBus
    
    with patch.object(EventBus, 'is_ready', new_callable=PropertyMock) as mock_is_ready:
        mock_is_ready.return_value = True
        yield mock_is_ready


@pytest.mark.asyncio
async def test_health_check_endpoint(async_client: AsyncClient):
    """Test standard health check returns 200 OK."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert "environment" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_ready_check_success(async_client: AsyncClient, mock_health_checks, mock_event_bus):
    """Test readiness check returns 200 when all services are healthy."""
    db_mock, redis_mock, queue_mock = mock_health_checks
    db_mock.return_value = HealthResult(status="healthy")
    redis_mock.return_value = HealthResult(status="healthy")
    queue_mock.return_value = HealthResult(status="healthy")

    response = await async_client.get("/api/v1/ready")
    
    data = response.json()
    print("\n\nRESPONSE DATA:", data, "\n\n")

    assert response.status_code == status.HTTP_200_OK
    assert data["status"] == "healthy"
    assert data["database"] == "healthy"
    assert data["redis"] == "healthy"
    assert data["queue"] == "healthy"
    assert data["events"] == "healthy"


@pytest.mark.asyncio
async def test_ready_check_failure(async_client: AsyncClient, mock_health_checks, mock_event_bus):
    """Test readiness check returns 503 when a service is unhealthy."""
    db_mock, redis_mock, queue_mock = mock_health_checks
    db_mock.return_value = HealthResult(status="unhealthy", error="DB Down")
    redis_mock.return_value = HealthResult(status="healthy")
    queue_mock.return_value = HealthResult(status="healthy")

    response = await async_client.get("/api/v1/ready")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "unhealthy"