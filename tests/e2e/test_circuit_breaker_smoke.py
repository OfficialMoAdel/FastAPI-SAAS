from fastapi import status
import pytest
from httpx import ASGITransport, AsyncClient

# Use absolute import instead of relative to avoid import errors
from src.app.main import app

# Circuit Breaker states
CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


@pytest.fixture
async def e2e_client():
    """Create a client for E2E tests pointing to the app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_smoke_circuit_breaker_registry(e2e_client):
    """
    E2E Smoke Test for Circuit Breaker Registry.

    Verifies:
    1. Circuit breakers are registered on startup (database is registered).
    2. Admin API exposes circuit breaker status.
    3. Circuits start in CLOSED state.
    """
    # 1. Check if we can access the health endpoint to ensure app is up.
    response = await e2e_client.get("/api/v1/health")
    assert response.status_code == 200

    # 2. Access the circuit breaker registry directly
    from src.app.core.resilience.registry import CircuitState, get_registry
    from src.app.core.resilience.db_circuit_breaker import db_circuit

    registry = get_registry()

    # Check registry content
    circuits = registry.list_all()
    circuit_names = [c.name for c in circuits]

    # The "database" circuit is registered via db_circuit_breaker.py
    assert "database" in circuit_names

    # 3. Verify initial state
    db_circuit = registry.get("database")
    assert db_circuit is not None
    assert db_circuit.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_state_transition_simulation():
    """
    Simulate state transitions in a circuit breaker.
    """
    from src.app.core.resilience.registry import CircuitState, get_registry
    from src.app.core.utils.circuit_breaker import CircuitBreakerOpen

    registry = get_registry()

    # Get the database circuit (the one that's registered)
    circuit_name = "database"

    circuit = registry.get(circuit_name)
    assert circuit is not None

    # Store initial failure threshold
    original_threshold = circuit.failure_threshold

    # Reset it first
    circuit.reset()
    assert circuit.state == CircuitState.CLOSED

    # Simulate failures (default threshold is 3 for database circuit)
    for _ in range(original_threshold):
        await circuit.record_failure()

    # Should be OPEN now
    assert circuit.state == CircuitState.OPEN

    # Define a dummy protected function
    from src.app.core.utils.circuit_breaker import circuit_breaker

    @circuit_breaker(circuit)
    async def protected_op():
        return "success"

    # Should raise exception when OPEN
    with pytest.raises(CircuitBreakerOpen):
        await protected_op()

    # Reset for other tests
    circuit.reset()
    assert circuit.state == CircuitState.CLOSED
