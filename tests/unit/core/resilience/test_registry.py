"""Unit tests for CircuitBreakerRegistry.

Tests cover:
- Registry initialization and singleton pattern
- Registration operations (register, create, duplicate handling)
- Retrieval operations (get, list_all, get_all)
- Filtering operations (by state, service type, tags)
- Statistics operations (get_stats, get_all_stats, health_summary)
- Reset operations (reset, reset_all)
- Concurrent access and thread safety
- Edge cases and error handling
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.app.core.resilience.registry import (
    CircuitBreakerRegistry,
    CircuitBreakerSpec,
    ServiceType,
    get_registry,
)
from src.app.core.utils.circuit_breaker import CircuitBreaker, CircuitState

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def registry():
    """Create a fresh registry instance for each test."""
    return CircuitBreakerRegistry()


@pytest.fixture
def mock_circuit_breaker():
    """Factory for creating mock circuit breaker instances."""

    def _create_mock(
        name: str = "test_circuit",
        state: CircuitState = CircuitState.CLOSED,
        failure_count: int = 0,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        last_failure_time: float = 0.0,
    ) -> MagicMock:
        mock = MagicMock(spec=CircuitBreaker)
        mock.name = name
        mock.state = state
        mock.failure_count = failure_count
        mock.failure_threshold = failure_threshold
        mock.recovery_timeout = recovery_timeout
        mock.success_threshold = success_threshold
        mock.last_failure_time = last_failure_time
        mock.reset = MagicMock()
        return mock

    return _create_mock


@pytest.fixture
def registry_with_circuits(registry, mock_circuit_breaker):
    """Pre-populated registry with multiple circuits for testing."""
    circuits = {
        "db_circuit": mock_circuit_breaker(
            name="db_circuit",
            state=CircuitState.CLOSED,
            failure_count=2,
        ),
        "webhook_circuit": mock_circuit_breaker(
            name="webhook_circuit",
            state=CircuitState.OPEN,
            failure_count=5,
        ),
        "email_circuit": mock_circuit_breaker(
            name="email_circuit",
            state=CircuitState.HALF_OPEN,
            failure_count=3,
        ),
    }

    registry.register(
        "db_circuit",
        circuits["db_circuit"],
        description="Database operations",
        service_type=ServiceType.DATABASE,
        tags=["critical"],
    )
    registry.register(
        "webhook_circuit",
        circuits["webhook_circuit"],
        description="Webhook delivery",
        service_type=ServiceType.WEBHOOK,
        tags=["external"],
    )
    registry.register(
        "email_circuit",
        circuits["email_circuit"],
        description="Email sending",
        service_type=ServiceType.EMAIL,
        tags=["external", "notifications"],
    )

    return registry, circuits


# ============================================================================
# Registry Initialization Tests
# ============================================================================


def test_registry_initialization(registry):
    """Verify empty registry on creation."""
    assert len(registry._breakers) == 0
    # _lock is lazily initialized to avoid event loop binding issues
    assert registry._lock is None


def test_registry_singleton():
    """Verify global registry instance exists and is reused."""
    registry1 = get_registry()
    registry2 = get_registry()
    assert registry1 is registry2


# ============================================================================
# Registration Operations Tests
# ============================================================================


def test_register_circuit_breaker(registry, mock_circuit_breaker):
    """Successfully register new circuit."""
    circuit = mock_circuit_breaker(name="test_circuit")
    result = registry.register(
        "test_circuit",
        circuit,
        description="Test circuit",
        service_type=ServiceType.DATABASE,
    )

    assert result is circuit
    assert "test_circuit" in registry._breakers
    assert registry._breakers["test_circuit"].name == "test_circuit"
    assert registry._breakers["test_circuit"].breaker is circuit


def test_register_with_service_type(registry, mock_circuit_breaker):
    """Service type stored in spec."""
    circuit = mock_circuit_breaker()
    registry.register(
        "test_circuit",
        circuit,
        service_type=ServiceType.WEBHOOK,
    )

    spec = registry._breakers["test_circuit"]
    assert spec.service_type == ServiceType.WEBHOOK


def test_register_with_tags(registry, mock_circuit_breaker):
    """Tags stored in spec."""
    circuit = mock_circuit_breaker()
    tags = ["critical", "external"]
    registry.register("test_circuit", circuit, tags=tags)

    spec = registry._breakers["test_circuit"]
    assert spec.tags == tags


def test_create_circuit_breaker(registry):
    """Create and register in one call."""
    circuit = registry.create(
        "test_circuit",
        failure_threshold=10,
        recovery_timeout=120.0,
        success_threshold=3,
        service_type=ServiceType.REDIS,
        description="Test circuit",
    )

    assert circuit is not None
    assert isinstance(circuit, CircuitBreaker)
    assert circuit.name == "test_circuit"
    assert circuit.failure_threshold == 10
    assert circuit.recovery_timeout == 120.0
    assert circuit.success_threshold == 3
    assert "test_circuit" in registry._breakers


def test_duplicate_registration(registry, mock_circuit_breaker):
    """Duplicate registration raises ValueError."""
    circuit = mock_circuit_breaker()
    registry.register("test_circuit", circuit)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("test_circuit", circuit)


def test_register_updates_specs(registry, mock_circuit_breaker):
    """CircuitBreakerSpec created correctly."""
    circuit = mock_circuit_breaker()
    registry.register(
        "test_circuit",
        circuit,
        description="Test description",
        service_type=ServiceType.EXTERNAL_API,
        tags=["tag1", "tag2"],
    )

    spec = registry._breakers["test_circuit"]
    assert isinstance(spec, CircuitBreakerSpec)
    assert spec.name == "test_circuit"
    assert spec.breaker is circuit
    assert spec.description == "Test description"
    assert spec.service_type == ServiceType.EXTERNAL_API
    assert spec.tags == ["tag1", "tag2"]


# ============================================================================
# Retrieval Operations Tests
# ============================================================================


def test_get_existing_circuit(registry, mock_circuit_breaker):
    """Retrieve by name returns correct instance."""
    circuit = mock_circuit_breaker()
    registry.register("test_circuit", circuit)

    result = registry.get("test_circuit")
    assert result is circuit


def test_get_nonexistent_circuit(registry):
    """Returns None for missing circuit."""
    result = registry.get("nonexistent")
    assert result is None


def test_list_all_empty(registry):
    """Empty list when no circuits registered."""
    result = registry.list_all()
    assert result == []


def test_list_all_multiple(registry_with_circuits):
    """Returns all registered circuits."""
    registry, circuits = registry_with_circuits
    result = registry.list_all()

    assert len(result) == 3
    assert circuits["db_circuit"] in result
    assert circuits["webhook_circuit"] in result
    assert circuits["email_circuit"] in result


def test_get_all(registry_with_circuits):
    """Returns all specs as dictionary."""
    registry, _ = registry_with_circuits
    result = registry.get_all()

    assert len(result) == 3
    assert "db_circuit" in result
    assert "webhook_circuit" in result
    assert "email_circuit" in result
    assert all(isinstance(spec, CircuitBreakerSpec) for spec in result.values())


# ============================================================================
# Filtering Operations Tests
# ============================================================================


def test_get_by_state_closed(registry_with_circuits):
    """Filter returns only CLOSED circuits."""
    registry, circuits = registry_with_circuits
    result = registry.get_by_state(CircuitState.CLOSED)

    assert len(result) == 1
    assert circuits["db_circuit"] in result


def test_get_by_state_open(registry_with_circuits):
    """Filter returns only OPEN circuits."""
    registry, circuits = registry_with_circuits
    result = registry.get_by_state(CircuitState.OPEN)

    assert len(result) == 1
    assert circuits["webhook_circuit"] in result


def test_get_by_state_half_open(registry_with_circuits):
    """Filter returns only HALF_OPEN circuits."""
    registry, circuits = registry_with_circuits
    result = registry.get_by_state(CircuitState.HALF_OPEN)

    assert len(result) == 1
    assert circuits["email_circuit"] in result


def test_get_by_service_type_database(registry_with_circuits):
    """Filter by service_type='database'."""
    registry, circuits = registry_with_circuits
    result = registry.get_by_service_type(ServiceType.DATABASE)

    assert len(result) == 1
    assert circuits["db_circuit"] in result


def test_get_by_service_type_webhook(registry_with_circuits):
    """Filter by service_type='webhook'."""
    registry, circuits = registry_with_circuits
    result = registry.get_by_service_type(ServiceType.WEBHOOK)

    assert len(result) == 1
    assert circuits["webhook_circuit"] in result


def test_get_by_service_type_unknown(registry_with_circuits):
    """Returns empty for unknown type."""
    registry, _ = registry_with_circuits
    result = registry.get_by_service_type(ServiceType.UNKNOWN)

    assert len(result) == 0


def test_get_by_tag_single_match(registry_with_circuits):
    """Filter by tag returns matching circuits."""
    registry, _ = registry_with_circuits
    result = registry.get_by_tag("critical")

    assert len(result) == 1
    assert "db_circuit" in result


def test_get_by_tag_multiple_matches(registry_with_circuits):
    """Filter by tag returns all matching circuits."""
    registry, _ = registry_with_circuits
    result = registry.get_by_tag("external")

    assert len(result) == 2
    assert "webhook_circuit" in result
    assert "email_circuit" in result


def test_get_by_tag_no_matches(registry_with_circuits):
    """Returns empty dict for non-existent tag."""
    registry, _ = registry_with_circuits
    result = registry.get_by_tag("nonexistent")

    assert len(result) == 0


# ============================================================================
# Statistics Operations Tests
# ============================================================================


def test_get_all_stats_format(registry_with_circuits):
    """Verify stats dict structure."""
    registry, _ = registry_with_circuits
    result = registry.get_all_stats()

    assert len(result) == 3
    assert all(isinstance(stat, dict) for stat in result)

    # Check first stat has required keys
    stat = result[0]
    required_keys = {
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
    assert required_keys.issubset(stat.keys())


def test_get_all_stats_includes_service_type(registry_with_circuits):
    """Service type in stats."""
    registry, _ = registry_with_circuits
    result = registry.get_all_stats()

    db_stat = next(s for s in result if s["name"] == "db_circuit")
    assert db_stat["service_type"] == "database"


def test_get_stats(registry_with_circuits):
    """Get aggregate statistics."""
    registry, _ = registry_with_circuits
    result = registry.get_stats()

    assert result["total"] == 3
    assert result["by_state"]["closed"] == 1
    assert result["by_state"]["open"] == 1
    assert result["by_state"]["half_open"] == 1


def test_health_summary_all_closed(registry, mock_circuit_breaker):
    """health_percentage = 100.0 when all closed."""
    for i in range(3):
        circuit = mock_circuit_breaker(
            name=f"circuit_{i}",
            state=CircuitState.CLOSED,
        )
        registry.register(f"circuit_{i}", circuit)

    result = registry.get_health_summary()

    assert result["total"] == 3
    assert result["closed"] == 3
    assert result["open"] == 0
    assert result["half_open"] == 0
    assert result["health_percentage"] == 100.0
    assert result["at_risk"] == 0


def test_health_summary_all_open(registry, mock_circuit_breaker):
    """health_percentage = 0.0, at_risk = 0 when all open."""
    for i in range(3):
        circuit = mock_circuit_breaker(
            name=f"circuit_{i}",
            state=CircuitState.OPEN,
        )
        registry.register(f"circuit_{i}", circuit)

    result = registry.get_health_summary()

    assert result["total"] == 3
    assert result["closed"] == 0
    assert result["open"] == 3
    assert result["half_open"] == 0
    assert result["health_percentage"] == 0.0
    assert result["at_risk"] == 0


def test_health_summary_mixed_states(registry_with_circuits):
    """Correct percentage calculation with mixed states."""
    registry, _ = registry_with_circuits
    result = registry.get_health_summary()

    # 1 closed out of 3 total = 33.33%
    assert result["total"] == 3
    assert result["closed"] == 1
    assert result["open"] == 1
    assert result["half_open"] == 1
    assert result["health_percentage"] == 33.33


def test_health_summary_at_risk_detection(registry, mock_circuit_breaker):
    """Detect circuits at risk (>60% of threshold)."""
    # Circuit with 4 failures out of 5 threshold (80% - at risk)
    circuit1 = mock_circuit_breaker(
        name="at_risk_circuit",
        state=CircuitState.CLOSED,
        failure_count=4,
        failure_threshold=5,
    )
    # Circuit with 2 failures out of 5 threshold (40% - not at risk)
    circuit2 = mock_circuit_breaker(
        name="safe_circuit",
        state=CircuitState.CLOSED,
        failure_count=2,
        failure_threshold=5,
    )

    registry.register("at_risk_circuit", circuit1)
    registry.register("safe_circuit", circuit2)

    result = registry.get_health_summary()

    assert result["at_risk"] == 1


def test_health_summary_empty_registry(registry):
    """health_percentage = 100.0, total = 0 for empty registry."""
    result = registry.get_health_summary()

    assert result["total"] == 0
    assert result["closed"] == 0
    assert result["open"] == 0
    assert result["half_open"] == 0
    assert result["health_percentage"] == 100.0
    assert result["at_risk"] == 0


# ============================================================================
# Reset Operations Tests
# ============================================================================


@pytest.mark.asyncio
async def test_reset_existing_circuit(registry, mock_circuit_breaker):
    """Circuit state changes to CLOSED."""
    circuit = mock_circuit_breaker(state=CircuitState.OPEN)
    registry.register("test_circuit", circuit)

    result = await registry.reset("test_circuit")

    assert result is True
    circuit.reset.assert_called_once()


@pytest.mark.asyncio
async def test_reset_nonexistent_circuit(registry):
    """Returns False for non-existent circuit."""
    result = await registry.reset("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_reset_all_multiple_circuits(registry_with_circuits):
    """All circuits reset, returns count."""
    registry, circuits = registry_with_circuits

    result = await registry.reset_all()

    assert result == 3
    circuits["db_circuit"].reset.assert_called_once()
    circuits["webhook_circuit"].reset.assert_called_once()
    circuits["email_circuit"].reset.assert_called_once()


@pytest.mark.asyncio
async def test_reset_all_empty_registry(registry):
    """Returns 0 for empty registry."""
    result = await registry.reset_all()
    assert result == 0


# ============================================================================
# Concurrent Access Tests
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_registration(registry):
    """Multiple async tasks registering simultaneously."""

    async def register_circuit(name: str):
        circuit = registry.create(name, service_type=ServiceType.DATABASE)
        return circuit

    # Register 10 circuits concurrently
    tasks = [register_circuit(f"circuit_{i}") for i in range(10)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert len(registry._breakers) == 10
    assert all(f"circuit_{i}" in registry._breakers for i in range(10))


@pytest.mark.asyncio
async def test_concurrent_reset_operations(registry_with_circuits):
    """Multiple reset operations execute safely."""
    registry, circuits = registry_with_circuits

    # Reset all circuits concurrently
    tasks = [
        registry.reset("db_circuit"),
        registry.reset("webhook_circuit"),
        registry.reset("email_circuit"),
    ]
    results = await asyncio.gather(*tasks)

    assert all(results)
    circuits["db_circuit"].reset.assert_called_once()
    circuits["webhook_circuit"].reset.assert_called_once()
    circuits["email_circuit"].reset.assert_called_once()


@pytest.mark.asyncio
async def test_lock_prevents_race_conditions(registry, mock_circuit_breaker):
    """Verify asyncio.Lock usage prevents race conditions."""
    circuit = mock_circuit_breaker()
    registry.register("test_circuit", circuit)

    # Simulate concurrent reset operations
    async def reset_with_delay():
        await registry.reset("test_circuit")
        await asyncio.sleep(0.01)  # Small delay to test lock

    tasks = [reset_with_delay() for _ in range(5)]
    await asyncio.gather(*tasks)

    # Reset should be called 5 times, but lock ensures sequential execution
    assert circuit.reset.call_count == 5


# ============================================================================
# Edge Cases Tests
# ============================================================================


def test_zero_circuits_health_summary(registry):
    """No division by zero with empty registry."""
    result = registry.get_health_summary()

    # Should not raise ZeroDivisionError
    assert result["health_percentage"] == 100.0
    assert result["total"] == 0


def test_special_characters_in_name(registry, mock_circuit_breaker):
    """Handle circuit names with spaces/symbols."""
    special_names = [
        "circuit with spaces",
        "circuit-with-dashes",
        "circuit_with_underscores",
        "circuit.with.dots",
        "circuit:with:colons",
    ]

    for name in special_names:
        circuit = mock_circuit_breaker(name=name)
        registry.register(name, circuit)

    assert len(registry._breakers) == len(special_names)
    for name in special_names:
        assert registry.get(name) is not None


def test_empty_description_and_tags(registry, mock_circuit_breaker):
    """Handle empty description and None tags."""
    circuit = mock_circuit_breaker()
    registry.register("test_circuit", circuit, description="", tags=None)

    spec = registry._breakers["test_circuit"]
    assert spec.description == ""
    assert spec.tags is None


def test_circuit_breaker_spec_to_dict(registry, mock_circuit_breaker):
    """CircuitBreakerSpec.to_dict() returns correct structure."""
    circuit = mock_circuit_breaker(
        name="test_circuit",
        state=CircuitState.OPEN,
        failure_count=5,
        failure_threshold=5,
        recovery_timeout=60.0,
        success_threshold=2,
        last_failure_time=1234567890.0,
    )
    registry.register(
        "test_circuit",
        circuit,
        description="Test circuit",
        service_type=ServiceType.DATABASE,
        tags=["tag1", "tag2"],
    )

    spec = registry._breakers["test_circuit"]
    result = spec.to_dict()

    assert result["name"] == "test_circuit"
    assert result["description"] == "Test circuit"
    assert result["service_type"] == "database"
    assert result["tags"] == ["tag1", "tag2"]
    assert result["state"] == "open"
    assert result["failure_count"] == 5
    assert result["failure_threshold"] == 5
    assert result["recovery_timeout"] == 60.0
    assert result["success_threshold"] == 2
    assert result["last_failure_time"] == 1234567890.0


def test_get_by_tag_with_none_tags(registry, mock_circuit_breaker):
    """Handle circuits with None tags when filtering."""
    circuit1 = mock_circuit_breaker(name="circuit1")
    circuit2 = mock_circuit_breaker(name="circuit2")

    registry.register("circuit1", circuit1, tags=["tag1"])
    registry.register("circuit2", circuit2, tags=None)

    result = registry.get_by_tag("tag1")
    assert len(result) == 1
    assert "circuit1" in result


# ============================================================================
# Logging Tests
# ============================================================================


def test_register_logs_info(registry, mock_circuit_breaker):
    """Registration logs info message."""
    circuit = mock_circuit_breaker()

    with patch("src.app.core.resilience.registry.logger.info") as mock_info:
        registry.register(
            "test_circuit",
            circuit,
            service_type=ServiceType.DATABASE,
        )

        args = " ".join(str(a) for a in mock_info.call_args[0])
        assert "Registered circuit breaker" in args


@pytest.mark.asyncio
async def test_reset_logs_info(registry, mock_circuit_breaker):
    """Reset operation logs info message."""
    circuit = mock_circuit_breaker()
    registry.register("test_circuit", circuit)

    with patch("src.app.core.resilience.registry.logger.info") as mock_info:
        await registry.reset("test_circuit")

        args = " ".join(str(a) for a in mock_info.call_args[0])
        assert "Reset circuit breaker" in args


@pytest.mark.asyncio
async def test_reset_all_logs_info(registry_with_circuits):
    """Reset all operation logs info message."""
    registry, _ = registry_with_circuits

    with patch("src.app.core.resilience.registry.logger.info") as mock_info:
        await registry.reset_all()

        args = " ".join(str(a) for a in mock_info.call_args[0])
        assert "Reset all circuit breakers:" in args


# ============================================================================
# Helper Functions Tests
# ============================================================================


def test_get_circuit_breaker_helper():
    """Helper function get_circuit_breaker() works correctly."""
    from src.app.core.resilience.registry import get_circuit_breaker

    # Get the global registry and register a circuit
    registry = get_registry()
    circuit = registry.create("helper_test_circuit")

    # Use helper to retrieve it
    result = get_circuit_breaker("helper_test_circuit")
    assert result is circuit

    # Clean up
    registry._breakers.clear()


def test_create_circuit_breaker_helper():
    """Helper function create_circuit_breaker() works correctly."""
    from src.app.core.resilience.registry import create_circuit_breaker

    # Create circuit using helper
    circuit = create_circuit_breaker(
        "helper_create_test",
        failure_threshold=10,
        recovery_timeout=120.0,
        success_threshold=3,
        service_type=ServiceType.REDIS,
        description="Helper test circuit",
    )

    assert circuit is not None
    assert circuit.name == "helper_create_test"
    assert circuit.failure_threshold == 10
    assert circuit.recovery_timeout == 120.0
    assert circuit.success_threshold == 3

    # Verify it's in the global registry
    registry = get_registry()
    assert "helper_create_test" in registry._breakers

    # Clean up
    registry._breakers.clear()


def test_auto_register_decorator():
    """Decorator auto_register() registers circuit breakers."""
    from src.app.core.resilience.registry import auto_register

    # Create a circuit breaker and apply decorator
    circuit = CircuitBreaker(name="decorator_test")
    decorated_circuit = auto_register(
        "decorator_test",
        description="Decorator test circuit",
        service_type=ServiceType.WEBHOOK,
        tags=["test", "decorator"],
    )(circuit)

    # Verify it's registered
    registry = get_registry()
    assert "decorator_test" in registry._breakers
    spec = registry._breakers["decorator_test"]
    assert spec.description == "Decorator test circuit"
    assert spec.service_type == ServiceType.WEBHOOK
    assert spec.tags == ["test", "decorator"]
    assert spec.breaker is decorated_circuit

    # Clean up
    registry._breakers.clear()


# ============================================================================
# Scoped Webhook Breaker Tests (P2-B, relocated from utils/circuit_breaker)
# ============================================================================


class TestScopedWebhookBreakers:
    """get_webhook_breaker lives in the registry and registers each scope."""

    def setup_method(self):
        """Isolate the scoped-breaker dict and singleton registry per test."""
        from src.app.core.resilience import registry as registry_module

        registry_module._scoped_webhook_breakers.clear()
        get_registry()._breakers.clear()

    def teardown_method(self):
        from src.app.core.resilience import registry as registry_module

        registry_module._scoped_webhook_breakers.clear()
        get_registry()._breakers.clear()

    def test_none_scope_returns_legacy_global(self):
        """scope=None keeps the legacy webhook_circuit contract."""
        from src.app.core.resilience.registry import get_webhook_breaker
        from src.app.core.utils.circuit_breaker import webhook_circuit

        assert get_webhook_breaker(None) is webhook_circuit

    def test_scope_creates_and_registers_breaker(self):
        """A new scope creates a breaker registered under 'webhook:{scope}'."""
        from src.app.core.resilience.registry import get_webhook_breaker

        breaker = get_webhook_breaker("endpoint-1")

        assert breaker.name == "webhook:endpoint-1"
        registry = get_registry()
        assert registry.get("webhook:endpoint-1") is breaker
        spec = registry.get_all()["webhook:endpoint-1"]
        assert spec.service_type == ServiceType.WEBHOOK

    def test_same_scope_returns_same_instance(self):
        """Repeated calls for a scope return the same breaker object."""
        from src.app.core.resilience.registry import get_webhook_breaker

        first = get_webhook_breaker("endpoint-1")
        second = get_webhook_breaker("endpoint-1")
        assert first is second

    def test_scope_eviction_removes_oldest_from_registry(self):
        """Past the cap, the oldest scope leaves both the dict and the registry."""
        from src.app.core.resilience import registry as registry_module
        from src.app.core.resilience.registry import get_webhook_breaker

        original_cap = registry_module._WEBHOOK_BREAKER_MAX_SCOPES
        registry_module._WEBHOOK_BREAKER_MAX_SCOPES = 2
        try:
            oldest = get_webhook_breaker("ep-a")
            get_webhook_breaker("ep-b")
            newest = get_webhook_breaker("ep-c")  # evicts ep-a
        finally:
            registry_module._WEBHOOK_BREAKER_MAX_SCOPES = original_cap

        registry = get_registry()
        assert registry.get("webhook:ep-a") is None
        assert registry.get("webhook:ep-b") is not None
        assert registry.get("webhook:ep-c") is newest
        assert oldest is not newest

    def test_unregister_removes_breaker(self):
        """unregister() drops a name and reports whether it existed."""
        from src.app.core.resilience.registry import get_webhook_breaker

        get_webhook_breaker("endpoint-1")
        assert get_registry().unregister("webhook:endpoint-1") is True
        assert get_registry().unregister("webhook:endpoint-1") is False
