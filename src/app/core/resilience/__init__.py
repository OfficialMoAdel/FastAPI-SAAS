"""Resilience patterns for fault-tolerant operations.

This module provides resilience patterns including:
- CircuitBreakerRegistry: Centralized management of circuit breakers
- CircuitBreakerSpec: Specification for registered circuit breakers
- ServiceType: Service categorization enum
- Helper functions for accessing the global registry
"""

from src.app.core.resilience.breakers import (
    arq_circuit,
    email_circuit,
    webhook_circuit,
)
from src.app.core.resilience.db_circuit_breaker import (
    DB_INFRASTRUCTURE_ERRORS,
    db_circuit,
    db_circuit_guard,
)
from src.app.core.resilience.registry import (
    CircuitBreakerRegistry,
    CircuitBreakerSpec,
    ServiceType,
    auto_register,
    create_circuit_breaker,
    get_circuit_breaker,
    get_registry,
    get_webhook_breaker,
)
from src.app.core.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    circuit_breaker,
)

__all__ = [
    # Registry classes
    "CircuitBreakerRegistry",
    "CircuitBreakerSpec",
    "ServiceType",
    # Registry functions
    "get_registry",
    "get_circuit_breaker",
    "create_circuit_breaker",
    "auto_register",
    "get_webhook_breaker",
    # Circuit breaker core (re-exported for convenience)
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "CircuitState",
    "circuit_breaker",
    # Database resilience
    "db_circuit",
    "db_circuit_guard",
    "DB_INFRASTRUCTURE_ERRORS",
    # Named singletons (see breakers.py for why construction lives there)
    "webhook_circuit",
    "email_circuit",
    "arq_circuit",
]


# ARCHITECTURE NOTE (audit §7/A19 resolution):
# This package is NOT a duplicate of core/utils/circuit_breaker.py.
#   - core/utils/circuit_breaker.py: generic CircuitBreaker ALGORITHM +
#     named instances for EXTERNAL services (arq, webhooks, email).
#   - core/resilience/: DB-domain layer built ON TOP of it — infra-error
#     classification, retry loops, guard() context manager, per-service
#     registry. It imports CircuitState from utils by design.
# Consolidating them would couple external-service breakers to DB semantics.
