"""The named circuit-breaker singletons production traffic actually rides.

Why this module exists
----------------------
``CircuitBreaker`` is the algorithm (``core/utils/circuit_breaker.py``). The
*named instances* used to live beside it, constructed with a bare
``CircuitBreaker(...)`` — which does **not** register. The admin panel reads
only the registry, so every breaker built that way was invisible to
operations: it could not be observed or reset during an incident. The
breakers that *were* visible were visible only because they are built with
``create_circuit_breaker()``, which registers as a side effect of construction.

So the defect was an asymmetry, not three missing rows: **construction that
registers versus construction that does not**. This module closes it by making
the registering constructor the only way a named singleton is built.

Why here and not in ``core/utils/circuit_breaker.py``
----------------------------------------------------
``registry.py`` imports ``CircuitBreaker`` from ``core/utils/circuit_breaker.py``
at module level, so that module cannot import the registry back — which is why
``_register_default_breakers()`` was a ``pass`` with a commented-out call.
Construction has to sit on the registry's side of that cycle.
``core.utils.circuit_breaker`` keeps its public names via a lazy ``__getattr__``
re-export, so every existing import site keeps working and keeps object identity.

What this module does NOT own
-----------------------------
The algorithm (``core/utils/circuit_breaker.py``), the DB-domain layer built on
top of it (``core/resilience/db_circuit_breaker.py``), and the registry itself
(``core/resilience/registry.py``). It owns the named instances and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.core.resilience.registry import (
    ServiceType,
    _scoped_webhook_breakers,
    create_circuit_breaker,
)
from src.app.core.utils.circuit_breaker import CircuitBreaker

__all__ = [
    "DefaultBreaker",
    "arq_circuit",
    "default_breakers",
    "email_circuit",
    "reset_all_circuit_breakers",
    "webhook_circuit",
]

# P2-B: per-endpoint webhook breakers live in ``registry.get_webhook_breaker``
# (``webhook:{scope}``), which registers each scope on first use. The breaker
# below is the legacy process-global: it is what ``get_webhook_breaker(None)``
# hands back, and it is registered here so the admin panel can see it too.
webhook_circuit = create_circuit_breaker(
    name="webhook",
    failure_threshold=5,
    recovery_timeout=60.0,
    success_threshold=2,
    service_type=ServiceType.WEBHOOK,
    description="Webhook delivery (legacy process-global; scoped breakers are webhook:{scope})",
)

email_circuit = create_circuit_breaker(
    name="email",
    failure_threshold=3,
    recovery_timeout=120.0,  # Email servers may need longer recovery
    success_threshold=1,
    service_type=ServiceType.EMAIL,
    description="SMTP email delivery",
)

arq_circuit = create_circuit_breaker(
    name="arq_queue",
    failure_threshold=5,
    recovery_timeout=30.0,  # Shorter timeout for Redis recovery
    success_threshold=2,
    service_type=ServiceType.REDIS,
    description="ARQ/Redis queue operations (EventBus)",
)


@dataclass(frozen=True)
class DefaultBreaker:
    """One row of the declared default set.

    A declared default is a breaker that must be visible to the admin panel
    whenever the registry exists — including after ``reset_registry()``, which
    the autouse fixture in ``tests/conftest.py`` calls before every test.
    """

    name: str
    breaker: CircuitBreaker
    service_type: ServiceType
    description: str


def reset_all_circuit_breakers() -> None:
    """Reset every named breaker for test isolation.

    Resets both the state and the ``asyncio.Lock`` of each breaker — call this
    in test fixtures to prevent 'Lock bound to different event loop' errors
    during coverage runs.

    It lives here because it has to know every named breaker, and this module
    is where the named breakers live. ``core.utils.circuit_breaker`` re-exports
    it lazily for the existing import sites.
    """
    from src.app.core.resilience.db_circuit_breaker import db_circuit

    for breaker in (webhook_circuit, email_circuit, arq_circuit, db_circuit):
        breaker.reset()
        breaker.reset_lock()
    # P2-B: scoped webhook breakers are registry-owned test-isolation state too.
    _scoped_webhook_breakers.clear()


def default_breakers() -> tuple[DefaultBreaker, ...]:
    """The declared default set — the breakers production traffic rides.

    The imports are deferred: ``db_circuit_breaker`` and
    ``token_blacklist_cache`` both import the registry, and this function is
    called from inside registry creation, so a module-level import here would
    close the cycle.
    """
    from src.app.core.resilience.db_circuit_breaker import db_circuit
    from src.app.core.security.token_blacklist_cache import token_cache_circuit

    return (
        DefaultBreaker(
            "webhook",
            webhook_circuit,
            ServiceType.WEBHOOK,
            "Webhook delivery (legacy process-global; scoped breakers are webhook:{scope})",
        ),
        DefaultBreaker("email", email_circuit, ServiceType.EMAIL, "SMTP email delivery"),
        DefaultBreaker("arq_queue", arq_circuit, ServiceType.REDIS, "ARQ/Redis queue operations (EventBus)"),
        DefaultBreaker("database", db_circuit, ServiceType.DATABASE, "Database connection circuit breaker"),
        DefaultBreaker(
            "token_cache_circuit",
            token_cache_circuit,
            ServiceType.REDIS,
            "Redis token-blacklist cache (auth revocation lookups)",
        ),
    )
