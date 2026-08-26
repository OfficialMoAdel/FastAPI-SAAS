"""Resilience patterns for fault-tolerant operations.

This module provides resilience patterns including:
- CircuitBreakerRegistry: Centralized management of circuit breakers
- CircuitBreakerSpec: Specification for registered circuit breakers
- ServiceType: Service categorization enum
- Helper functions for accessing the global registry
"""

from src.app.core.resilience.db_circuit_breaker import (
    db_circuit,
    execute_query_with_circuit_breaker,
    execute_transaction_with_circuit_breaker,
    get_read_only_fallback,
)
from src.app.core.resilience.registry import (
    CircuitBreakerRegistry,
    CircuitBreakerSpec,
    ServiceType,
    auto_register,
    create_circuit_breaker,
    get_circuit_breaker,
    get_registry,
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
    # Circuit breaker core (re-exported for convenience)
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "CircuitState",
    "circuit_breaker",
    # Database resilience
    "db_circuit",
    "execute_query_with_circuit_breaker",
    "execute_transaction_with_circuit_breaker",
    "get_read_only_fallback",
]


# ARCHITECTURE NOTE (audit §7/A19 resolution):
# This package is NOT a duplicate of core/utils/circuit_breaker.py.
#   - core/utils/circuit_breaker.py: generic CircuitBreaker ALGORITHM +
#     named instances for EXTERNAL services (arq, webhooks, email).
#   - core/resilience/: DB-domain layer built ON TOP of it — infra-error
#     classification, retry loops, guard() context manager, per-service
#     registry. It imports CircuitState from utils by design.
# Consolidating them would couple external-service breakers to DB semantics.
