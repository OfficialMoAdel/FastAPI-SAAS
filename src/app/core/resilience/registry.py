"""Circuit breaker registry for centralized management and monitoring.

This module provides a global registry for all circuit breakers in the application,
enabling centralized configuration, monitoring, and control.
"""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.app.core.logger import get_logger
from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
)

logger = get_logger(__name__)


class ServiceType(str, Enum):
    """Service categories for circuit breakers."""

    DATABASE = "database"
    REDIS = "redis"
    EXTERNAL_API = "external_api"
    WEBHOOK = "webhook"
    EMAIL = "email"
    UNKNOWN = "unknown"


@dataclass
class CircuitBreakerSpec:
    """Specification for a circuit breaker instance.

    Attributes
    ----------
        name: Unique identifier for the circuit breaker
        breaker: The circuit breaker instance
        description: Human-readable description of what this protects
        service_type: Category of service (database, redis, external_api, etc.)
        tags: Optional tags for categorization
    """

    name: str
    breaker: CircuitBreaker
    description: str
    service_type: ServiceType = ServiceType.UNKNOWN
    tags: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert spec to dictionary for API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "service_type": self.service_type.value,
            "tags": self.tags or [],
            "state": self.breaker.state.value,
            "failure_count": self.breaker.failure_count,
            "failure_threshold": self.breaker.failure_threshold,
            "recovery_timeout": self.breaker.recovery_timeout,
            "success_threshold": self.breaker.success_threshold,
            "last_failure_time": self.breaker.last_failure_time,
        }


class CircuitBreakerRegistry:
    """Global registry for circuit breakers.

    Provides centralized management of all circuit breakers in the application,
    enabling monitoring, reset operations, and state inspection.
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreakerSpec] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Get or create lock in current event loop.

        Lazy initialization prevents lock binding to wrong event loop
        when registry singleton is created at import time.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register(
        self,
        name: str,
        breaker: CircuitBreaker,
        description: str = "",
        service_type: ServiceType = ServiceType.UNKNOWN,
        tags: list[str] | None = None,
    ) -> CircuitBreaker:
        """Register a circuit breaker with the registry.

        Parameters
        ----------
            name: Unique identifier for the circuit breaker
            breaker: The circuit breaker instance to register
            description: Human-readable description
            service_type: Category of service
            tags: Optional categorization tags

        Returns
        -------
            The registered circuit breaker instance
        """
        if name in self._breakers:
            raise ValueError(f"Circuit breaker '{name}' already registered")

        self._breakers[name] = CircuitBreakerSpec(
            name=name,
            breaker=breaker,
            description=description,
            service_type=service_type,
            tags=tags,
        )

        logger.info("Registered circuit breaker", name=name, type=service_type.value)

        return breaker

    def unregister(self, name: str) -> bool:
        """Remove a circuit breaker from the registry.

        Parameters
        ----------
            name: Unique identifier for the circuit breaker to remove

        Returns
        -------
            True if a breaker was removed, False if the name was not registered
        """
        return self._breakers.pop(name, None) is not None

    def create(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        service_type: ServiceType = ServiceType.UNKNOWN,
        description: str = "",
        tags: list[str] | None = None,
    ) -> CircuitBreaker:
        """Create and register a new circuit breaker.

        Parameters
        ----------
            name: Unique identifier for the circuit breaker
            failure_threshold: Max failures before opening
            recovery_timeout: Seconds before attempting recovery
            success_threshold: Successes needed to close from half-open
            service_type: Category of service
            description: Human-readable description
            tags: Optional categorization tags

        Returns
        -------
            The created circuit breaker instance
        """
        breaker = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            success_threshold=success_threshold,
        )

        return self.register(
            name=name,
            breaker=breaker,
            description=description,
            service_type=service_type,
            tags=tags,
        )

    def get(self, name: str) -> CircuitBreaker | None:
        """Get a circuit breaker by name.

        Parameters
        ----------
            name: The circuit breaker identifier

        Returns
        -------
            The circuit breaker instance, or None if not found
        """
        spec = self._breakers.get(name)
        return spec.breaker if spec else None

    def get_all(self) -> dict[str, CircuitBreakerSpec]:
        """Get all registered circuit breaker specs.

        Returns
        -------
            Dictionary mapping names to circuit breaker specs
        """
        return self._breakers.copy()

    def get_all_stats(self) -> list[dict[str, Any]]:
        """Get statistics for all circuit breakers.

        Returns
        -------
            List of statistics dictionaries for all circuits
        """
        return [spec.to_dict() for spec in self._breakers.values()]

    def get_by_tag(self, tag: str) -> dict[str, CircuitBreakerSpec]:
        """Get all circuit breakers with a specific tag.

        Parameters
        ----------
            tag: The tag to filter by

        Returns
        -------
            Dictionary of circuit breakers with the specified tag
        """
        return {name: spec for name, spec in self._breakers.items() if spec.tags and tag in spec.tags}

    def get_by_service_type(self, service_type: ServiceType) -> list[CircuitBreaker]:
        """Get all circuit breakers for a specific service type.

        Parameters
        ----------
            service_type: The service type to filter by

        Returns
        -------
            List of circuit breakers for the specified service type
        """
        return [spec.breaker for spec in self._breakers.values() if spec.service_type == service_type]

    async def reset(self, name: str) -> bool:
        """Reset a circuit breaker to closed state.

        Parameters
        ----------
            name: The circuit breaker identifier

        Returns
        -------
            True if reset successful, False if circuit breaker not found
        """
        spec = self._breakers.get(name)
        if not spec:
            return False

        async with self._get_lock():
            spec.breaker.reset()

        logger.info("Reset circuit breaker:", name=name)
        return True

    async def reset_all(self) -> int:
        """Reset all circuit breakers to closed state.

        Returns
        -------
            Number of circuit breakers reset
        """
        async with self._get_lock():
            for spec in self._breakers.values():
                spec.breaker.reset()

        count = len(self._breakers)
        logger.info("Reset all circuit breakers: total", count=count)
        return count

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics for all circuit breakers.

        Returns
        -------
            Dictionary containing aggregate stats
        """
        total = len(self._breakers)
        states = dict.fromkeys(CircuitState, 0)

        for spec in self._breakers.values():
            states[spec.breaker.state] += 1

        return {
            "total": total,
            "by_state": {state.value: count for state, count in states.items()},
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Get health summary for dashboard metrics.

        Returns
        -------
            Dictionary with health metrics including:
            - total: Total number of circuit breakers
            - closed: Number in CLOSED state
            - open: Number in OPEN state
            - half_open: Number in HALF_OPEN state
            - health_percentage: Percentage of circuits in healthy state
            - at_risk: Number of circuits approaching failure threshold
        """
        total = len(self._breakers)
        closed = 0
        open_count = 0
        half_open = 0
        at_risk = 0

        for spec in self._breakers.values():
            state = spec.breaker.state
            if state == CircuitState.CLOSED:
                closed += 1
                # Consider at risk if > 60% of threshold
                if spec.breaker.failure_count > (spec.breaker.failure_threshold * 0.6):
                    at_risk += 1
            elif state == CircuitState.OPEN:
                open_count += 1
            elif state == CircuitState.HALF_OPEN:
                half_open += 1

        health_percentage = (closed / total * 100) if total > 0 else 100.0

        return {
            "total": total,
            "closed": closed,
            "open": open_count,
            "half_open": half_open,
            "health_percentage": round(health_percentage, 2),
            "at_risk": at_risk,
        }


# Global singleton registry instance
_registry: CircuitBreakerRegistry | None = None


def get_registry() -> CircuitBreakerRegistry:
    """Get the global circuit breaker registry instance.

    Returns
    -------
        The singleton registry instance
    """
    global _registry
    if _registry is None:
        _registry = CircuitBreakerRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (for test isolation) and restore the defaults.

    Clears the singleton and forces recreation on next access.
    This prevents asyncio.Lock binding issues when event loops change.

    The recreation replays :func:`default_breakers`, because clearing the
    registry used to drop every named singleton with it — including
    ``database``, which ``tests/conftest.py`` then hand-registered back. Without
    the replay, a breaker disappears from the admin panel for the whole of
    every test run: the test suite was compensating for a defect instead of
    production owning the invariant.
    """
    global _registry
    if _registry is not None:
        _registry._breakers.clear()
        # Note: We do NOT set _registry._lock = None to prevent stranding
        # coroutines that are currently waiting on the lock.
    _registry = None
    # Scoped webhook breakers are registry-owned state too: if they survive the
    # reset while their registrations do not, get_webhook_breaker() would hand
    # back a cached breaker that is no longer registered — invisible again.
    _scoped_webhook_breakers.clear()
    _register_defaults(get_registry())


def _register_defaults(registry: CircuitBreakerRegistry) -> None:
    """Replay the declared default set into ``registry``.

    Every breaker built with :func:`create_circuit_breaker` registers itself as
    a side effect of construction, so a freshly imported process is already
    complete. This replay exists for the case where construction already
    happened and the registry did not: after :func:`reset_registry`.
    """
    # Deferred: breakers imports this module (for create_circuit_breaker), so a
    # module-level import would close the cycle.
    from src.app.core.resilience.breakers import default_breakers

    for row in default_breakers():
        # Replayed rows are module-level singletons: a previous test (or prior
        # uptime) may have left failure counts / OPEN state behind. Reset on
        # replay so reset_registry() restores defaults, not just registrations.
        row.breaker.reset()
        if registry.get(row.name) is None:
            registry.register(
                name=row.name,
                breaker=row.breaker,
                service_type=row.service_type,
                description=row.description,
            )


def get_circuit_breaker(name: str) -> CircuitBreaker | None:
    """Helper to get a circuit breaker from the global registry.

    Parameters
    ----------
        name: The circuit breaker identifier

    Returns
    -------
        The circuit breaker instance, or None if not found
    """
    return get_registry().get(name)


def create_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    success_threshold: int = 2,
    service_type: ServiceType = ServiceType.UNKNOWN,
    description: str = "",
) -> CircuitBreaker:
    """Helper to create and register a circuit breaker in the global registry.

    Parameters
    ----------
        name: Unique identifier
        failure_threshold: Max failures before opening
        recovery_timeout: Seconds before recovery attempt
        success_threshold: Successes to close from half-open
        service_type: Service category
        description: Human-readable description

    Returns
    -------
        The created circuit breaker instance
    """
    return get_registry().create(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        success_threshold=success_threshold,
        service_type=service_type,
        description=description,
    )


def auto_register(
    name: str,
    description: str = "",
    service_type: ServiceType = ServiceType.UNKNOWN,
    tags: list[str] | None = None,
) -> Callable[[CircuitBreaker], CircuitBreaker]:
    """Decorator to automatically register a circuit breaker.

    Usage:
        @auto_register("webhook", description="Webhook delivery", service_type=ServiceType.WEBHOOK)
        webhook_circuit = CircuitBreaker(name="webhook")

    Parameters
    ----------
        name: Unique identifier for the circuit breaker
        description: Human-readable description
        service_type: Service category
        tags: Optional categorization tags

    Returns
    -------
        Decorator function
    """

    def decorator(breaker: CircuitBreaker) -> CircuitBreaker:
        registry = get_registry()
        registry.register(
            name=name,
            breaker=breaker,
            description=description,
            service_type=service_type,
            tags=tags,
        )
        return breaker

    return decorator


# ---------------------------------------------------------------------------
# Scoped webhook breakers (P2-B, relocated from core/utils/circuit_breaker.py)
#
# Per-endpoint breakers isolate blast radius: one dead receiver must not trip
# deliveries for every other tenant/endpoint sharing the process. Each scope
# is registered under ``webhook:{scope}`` so the admin panel (which reads this
# registry) reports the breakers production traffic actually rides — the
# ADR-0001 "two registries, divergent state" failure mode.
# ---------------------------------------------------------------------------

# Bounded so a tenant/endpoint-cardinality explosion cannot grow this without
# limit; the oldest scope is evicted past the cap.
_WEBHOOK_BREAKER_MAX_SCOPES = 1024
_scoped_webhook_breakers: dict[str, CircuitBreaker] = {}
# Sync check-then-act guard: first-use creation runs no awaits (asyncio-atomic),
# but worker threads share this dict — one lock keeps construction+registration
# atomic so two threads cannot build-and-register competing breakers per scope.
_scoped_webhook_lock = threading.Lock()


def get_webhook_breaker(scope: str | None = None) -> CircuitBreaker:
    """Return the circuit breaker for a webhook delivery scope.

    Args:
        scope: Endpoint identifier (e.g. the endpoint id). ``None``
            returns the legacy process-global ``webhook_circuit``.

    Returns:
        The scoped breaker, creating (and registering) it on first use.
    """
    if scope is None:
        # Deferred: ``breakers`` imports this module (for create_circuit_breaker),
        # so importing it at module level would close the cycle.
        from src.app.core.resilience.breakers import webhook_circuit

        return webhook_circuit

    registry = get_registry()
    with _scoped_webhook_lock:
        breaker = _scoped_webhook_breakers.get(scope)
        if breaker is None:
            if len(_scoped_webhook_breakers) >= _WEBHOOK_BREAKER_MAX_SCOPES:
                oldest_scope = next(iter(_scoped_webhook_breakers))
                del _scoped_webhook_breakers[oldest_scope]
                registry.unregister(f"webhook:{oldest_scope}")
            breaker = CircuitBreaker(
                name=f"webhook:{scope}",
                failure_threshold=5,
                recovery_timeout=60.0,
                success_threshold=2,
            )
            _scoped_webhook_breakers[scope] = breaker
            registry.register(
                f"webhook:{scope}",
                breaker,
                description="Per-endpoint webhook delivery breaker",
                service_type=ServiceType.WEBHOOK,
            )
        return breaker
