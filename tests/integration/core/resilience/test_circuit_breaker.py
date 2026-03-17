"""Integration tests for circuit breaker admin API endpoints.

Tests the full HTTP request/response cycle for circuit breaker management,
including health summaries, individual circuit details, and reset operations.

Note: These tests use the router directly without database dependencies since
circuit breakers are in-memory only. This avoids tenant middleware issues.
"""

import time

import pytest
from fastapi.testclient import TestClient

from src.app.api.v1.admin.circuit_breakers import router
from src.app.core.resilience.registry import ServiceType, get_registry
from src.app.core.utils.circuit_breaker import CircuitBreaker, CircuitState

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def test_client():
    """Test client for circuit breaker router without tenant middleware."""
    # Create a minimal FastAPI app with just the circuit breaker router
    from fastapi import FastAPI
    from unittest.mock import MagicMock

    from src.app.api.dependencies import get_current_superuser

    app = FastAPI()
    # The tests expect the full path /api/v1/admin/circuit-breakers/...
    # The router provides /circuit-breakers
    # So we need to mount it under /api/v1/admin
    app.include_router(router, prefix="/api/v1/admin")

    # Override the superuser dependency so integration tests don't need real auth
    mock_superuser = MagicMock()
    mock_superuser.is_superuser = True
    app.dependency_overrides[get_current_superuser] = lambda: mock_superuser

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear circuit breaker registry before and after each test."""
    registry = get_registry()
    registry._breakers.clear()
    yield
    registry._breakers.clear()


@pytest.fixture
def sample_circuits():
    """Register sample circuit breakers for testing."""
    registry = get_registry()

    # Create circuits with different states
    db_circuit = CircuitBreaker(name="db_circuit", failure_threshold=5)
    webhook_circuit = CircuitBreaker(name="webhook_circuit", failure_threshold=5)
    email_circuit = CircuitBreaker(name="email_circuit", failure_threshold=5)

    # Set different states
    db_circuit._state = CircuitState.CLOSED
    db_circuit._failure_count = 2

    webhook_circuit._state = CircuitState.OPEN
    webhook_circuit._failure_count = 5
    webhook_circuit._last_failure_time = time.time()

    email_circuit._state = CircuitState.HALF_OPEN
    email_circuit._failure_count = 3

    # Register circuits
    registry.register(
        "db_circuit",
        db_circuit,
        description="Database operations",
        service_type=ServiceType.DATABASE,
    )
    registry.register(
        "webhook_circuit",
        webhook_circuit,
        description="Webhook delivery",
        service_type=ServiceType.WEBHOOK,
    )
    registry.register(
        "email_circuit",
        email_circuit,
        description="Email sending",
        service_type=ServiceType.EMAIL,
    )

    return {
        "db_circuit": db_circuit,
        "webhook_circuit": webhook_circuit,
        "email_circuit": email_circuit,
    }


# ============================================================================
# GET / Health Summary Endpoint Tests
# ============================================================================


def test_get_health_summary_empty_registry(test_client):
    """Returns correct structure with 0 circuits."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 0
    assert data["closed"] == 0
    assert data["open"] == 0
    assert data["half_open"] == 0
    assert data["health_percentage"] == 100.0
    assert data["at_risk"] == 0
    assert data["circuit_breakers"] == []


def test_get_health_summary_with_circuits(test_client, sample_circuits):
    """Accurate counts and percentages with multiple circuits."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert data["closed"] == 1
    assert data["open"] == 1
    assert data["half_open"] == 1
    assert data["health_percentage"] == 33.33  # 1 closed out of 3
    assert isinstance(data["circuit_breakers"], list)
    assert len(data["circuit_breakers"]) == 3


def test_get_health_summary_response_schema(test_client, sample_circuits):
    """Verify all required fields present."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    # Check top-level fields
    required_fields = {
        "total",
        "closed",
        "open",
        "half_open",
        "health_percentage",
        "at_risk",
        "circuit_breakers",
    }
    assert required_fields.issubset(data.keys())

    # Check circuit breaker detail fields
    if data["circuit_breakers"]:
        circuit = data["circuit_breakers"][0]
        circuit_fields = {
            "name",
            "description",
            "service_type",
            "tags",
            "state",
            "failure_count",
            "failure_threshold",
            "recovery_timeout",
            "success_threshold",
            "last_failure_time",
        }
        assert circuit_fields.issubset(circuit.keys())


def test_get_health_summary_status_code(test_client):
    """Returns 200 OK."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")
    assert response.status_code == 200


# ============================================================================
# GET /{name} Individual Circuit Endpoint Tests
# ============================================================================


def test_get_circuit_breaker_existing(test_client, sample_circuits):
    """Returns circuit details correctly."""
    response = test_client.get("/api/v1/admin/circuit-breakers/db_circuit")

    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "db_circuit"
    assert data["state"] == "closed"
    assert data["failure_count"] == 2
    assert data["failure_threshold"] == 5
    assert data["service_type"] == "database"
    assert data["description"] == "Database operations"


def test_get_circuit_breaker_response_schema(test_client, sample_circuits):
    """Verify all required fields present."""
    response = test_client.get("/api/v1/admin/circuit-breakers/webhook_circuit")

    assert response.status_code == 200
    data = response.json()

    required_fields = {
        "name",
        "state",
        "failure_count",
        "last_failure_time",
        "failure_threshold",
        "recovery_timeout",
        "success_threshold",
        "service_type",
        "description",
    }
    assert required_fields.issubset(data.keys())


def test_get_circuit_breaker_not_found(test_client):
    """Returns 404 with error message."""
    response = test_client.get("/api/v1/admin/circuit-breakers/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()
    assert "nonexistent" in data["detail"]


def test_get_circuit_breaker_includes_service_type(test_client, sample_circuits):
    """service_type field populated from spec."""
    response = test_client.get("/api/v1/admin/circuit-breakers/webhook_circuit")

    assert response.status_code == 200
    data = response.json()
    assert data["service_type"] == "webhook"


# ============================================================================
# POST /{name}/reset Single Reset Endpoint Tests
# ============================================================================


def test_reset_circuit_breaker_existing(test_client, sample_circuits):
    """Returns success response."""
    response = test_client.post("/api/v1/admin/circuit-breakers/webhook_circuit/reset")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "reset"
    assert data["name"] == "webhook_circuit"


def test_reset_circuit_breaker_changes_state(test_client, sample_circuits):
    """Verify circuit state becomes CLOSED."""
    # Verify initial state is OPEN
    circuit = sample_circuits["webhook_circuit"]
    assert circuit._state == CircuitState.OPEN

    # Reset the circuit
    response = test_client.post("/api/v1/admin/circuit-breakers/webhook_circuit/reset")
    assert response.status_code == 200

    # Verify state changed to CLOSED
    assert circuit._state == CircuitState.CLOSED
    assert circuit._failure_count == 0


def test_reset_circuit_breaker_not_found(test_client):
    """Returns 404 for non-existent circuit."""
    response = test_client.post("/api/v1/admin/circuit-breakers/nonexistent/reset")

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


def test_reset_circuit_breaker_status_code(test_client, sample_circuits):
    """Returns 200 OK."""
    response = test_client.post("/api/v1/admin/circuit-breakers/db_circuit/reset")
    assert response.status_code == 200


# ============================================================================
# POST /reset-all Batch Reset Endpoint Tests
# ============================================================================


def test_reset_all_circuit_breakers(test_client, sample_circuits):
    """Returns correct count of reset circuits."""
    response = test_client.post("/api/v1/admin/circuit-breakers/reset-all")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "reset"
    assert data["count"] == 3


def test_reset_all_changes_all_states(test_client, sample_circuits):
    """All circuits become CLOSED."""
    circuits = sample_circuits

    # Verify initial states are different
    assert circuits["db_circuit"]._state == CircuitState.CLOSED
    assert circuits["webhook_circuit"]._state == CircuitState.OPEN
    assert circuits["email_circuit"]._state == CircuitState.HALF_OPEN

    # Reset all
    response = test_client.post("/api/v1/admin/circuit-breakers/reset-all")
    assert response.status_code == 200

    # Verify all are now CLOSED
    assert circuits["db_circuit"]._state == CircuitState.CLOSED
    assert circuits["webhook_circuit"]._state == CircuitState.CLOSED
    assert circuits["email_circuit"]._state == CircuitState.CLOSED

    # Verify failure counts reset
    assert circuits["db_circuit"]._failure_count == 0
    assert circuits["webhook_circuit"]._failure_count == 0
    assert circuits["email_circuit"]._failure_count == 0


def test_reset_all_empty_registry(test_client):
    """Returns count = 0 for empty registry."""
    response = test_client.post("/api/v1/admin/circuit-breakers/reset-all")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "reset"
    assert data["count"] == 0


def test_reset_all_status_code(test_client, sample_circuits):
    """Returns 200 OK."""
    response = test_client.post("/api/v1/admin/circuit-breakers/reset-all")
    assert response.status_code == 200


# ============================================================================
# Integration Scenarios Tests
# ============================================================================


def test_full_workflow_register_query_reset(test_client):
    """Register circuit -> GET details -> Reset -> Verify."""
    registry = get_registry()

    # Step 1: Register a circuit
    circuit = registry.create(
        "test_workflow",
        failure_threshold=3,
        service_type=ServiceType.EXTERNAL_API,
        description="Test workflow circuit",
    )
    circuit._state = CircuitState.OPEN
    circuit._failure_count = 3
    circuit._last_failure_time = time.time()

    # Step 2: Query circuit details
    response = test_client.get("/api/v1/admin/circuit-breakers/test_workflow")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "open"
    assert data["failure_count"] == 3

    # Step 3: Reset the circuit
    response = test_client.post("/api/v1/admin/circuit-breakers/test_workflow/reset")
    assert response.status_code == 200

    # Step 4: Verify reset worked
    response = test_client.get("/api/v1/admin/circuit-breakers/test_workflow")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "closed"
    assert data["failure_count"] == 0


def test_multiple_circuits_health_calculation(test_client, sample_circuits):
    """Register 3 circuits with different states, verify health_percentage."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    # 1 closed, 1 open, 1 half_open = 33.33% health
    assert data["total"] == 3
    assert data["closed"] == 1
    assert data["open"] == 1
    assert data["half_open"] == 1
    assert data["health_percentage"] == 33.33


def test_service_type_filtering_via_api(test_client, sample_circuits):
    """Verify service_type visible in responses."""
    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    # Find each circuit and verify service_type
    circuits_by_name = {cb["name"]: cb for cb in data["circuit_breakers"]}

    assert circuits_by_name["db_circuit"]["service_type"] == "database"
    assert circuits_by_name["webhook_circuit"]["service_type"] == "webhook"
    assert circuits_by_name["email_circuit"]["service_type"] == "email"


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_invalid_circuit_name_format(test_client):
    """Special characters handled correctly."""
    registry = get_registry()

    # Register circuit with special characters
    special_name = "circuit-with-special_chars.test"
    registry.create(special_name, service_type=ServiceType.REDIS)

    # Should be able to query it
    response = test_client.get(f"/api/v1/admin/circuit-breakers/{special_name}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == special_name


def test_concurrent_api_requests(test_client, sample_circuits):
    """Multiple simultaneous requests don't cause race conditions."""
    from concurrent.futures import ThreadPoolExecutor

    def make_request(endpoint: str):
        return test_client.get(endpoint)

    # Make 10 concurrent requests to different endpoints
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/db_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/webhook_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/email_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/db_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/webhook_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/email_circuit"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/"),
            executor.submit(make_request, "/api/v1/admin/circuit-breakers/db_circuit"),
        ]

        results = [future.result() for future in futures]

    # All requests should succeed
    assert all(r.status_code == 200 for r in results)


# ============================================================================
# Additional Edge Cases
# ============================================================================


def test_health_summary_includes_circuit_list(test_client):
    """Health summary includes detailed circuit list."""
    registry = get_registry()
    registry.create("test1", service_type=ServiceType.DATABASE)
    registry.create("test2", service_type=ServiceType.REDIS)

    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()

    assert "circuit_breakers" in data
    assert len(data["circuit_breakers"]) == 2
    assert any(cb["name"] == "test1" for cb in data["circuit_breakers"])
    assert any(cb["name"] == "test2" for cb in data["circuit_breakers"])


def test_circuit_breaker_description_in_response(test_client, sample_circuits):
    """Description field included in individual circuit response."""
    response = test_client.get("/api/v1/admin/circuit-breakers/db_circuit")

    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "Database operations"


def test_reset_idempotency(test_client, sample_circuits):
    """Resetting an already closed circuit is safe."""
    circuit = sample_circuits["db_circuit"]
    assert circuit._state == CircuitState.CLOSED

    # Reset already closed circuit
    response = test_client.post("/api/v1/admin/circuit-breakers/db_circuit/reset")
    assert response.status_code == 200

    # Should still be closed
    assert circuit._state == CircuitState.CLOSED


def test_at_risk_detection_in_health_summary(test_client):
    """Health summary correctly identifies at-risk circuits."""
    registry = get_registry()

    # Create circuit at risk (4 failures out of 5 threshold = 80%)
    at_risk_circuit = CircuitBreaker(name="at_risk", failure_threshold=5)
    at_risk_circuit._state = CircuitState.CLOSED
    at_risk_circuit._failure_count = 4

    # Create safe circuit (2 failures out of 5 threshold = 40%)
    safe_circuit = CircuitBreaker(name="safe", failure_threshold=5)
    safe_circuit._state = CircuitState.CLOSED
    safe_circuit._failure_count = 2

    registry.register("at_risk", at_risk_circuit)
    registry.register("safe", safe_circuit)

    response = test_client.get("/api/v1/admin/circuit-breakers/")

    assert response.status_code == 200
    data = response.json()
    assert data["at_risk"] == 1  # Only the at_risk circuit
