"""The default breaker set — the seam that keeps every named breaker visible.

The admin panel reads only the registry, so a breaker that is not registered
cannot be observed or reset during an incident. That was the defect: ``email``
and ``arq_queue`` carried production traffic while being absent from the
registry, because they were built with a bare ``CircuitBreaker(...)`` instead
of ``create_circuit_breaker()``.

These tests pin the invariant rather than the implementation: **after a reset,
every declared default is in the registry**, and **the admin reset-all path
reaches every declared default.** Both fail before the fix and pass after it.
"""

from __future__ import annotations

import pytest

from src.app.core.resilience.breakers import default_breakers
from src.app.core.resilience.registry import (
    CircuitState,
    get_registry,
    get_webhook_breaker,
    reset_registry,
)
from src.app.core.utils.circuit_breaker import webhook_circuit

DECLARED_NAMES = {"webhook", "email", "arq_queue", "database", "token_cache_circuit"}


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    yield
    reset_registry()


def test_declared_defaults_match_the_table():
    """The table is the single declaration of which breakers exist."""
    assert {row.name for row in default_breakers()} == DECLARED_NAMES


def test_every_declared_default_is_registered():
    """The defect: email and arq_queue were absent from the registry."""
    registered = set(get_registry().get_all())
    missing = DECLARED_NAMES - registered
    assert not missing, f"declared breakers missing from the registry: {sorted(missing)}"


def test_defaults_survive_a_registry_reset():
    """reset_registry() must not hide a breaker from the admin panel.

    The autouse fixture in tests/conftest.py calls reset_registry() before every
    test, so a default that does not survive this is invisible for the whole of
    every test run.
    """
    reset_registry()
    registered = set(get_registry().get_all())
    assert DECLARED_NAMES <= registered


def test_the_defect_itself_email_and_arq_are_visible():
    """Named after the reported defect so the regression is unmistakable."""
    names = set(get_registry().get_all())
    assert "email" in names
    assert "arq_queue" in names


def test_admin_stats_surface_every_default():
    """get_all_stats() is what GET /admin/circuit-breakers serves."""
    served = {row["name"] for row in get_registry().get_all_stats()}
    assert DECLARED_NAMES <= served


async def test_reset_all_reaches_every_default():
    """POST /admin/circuit-breakers/reset-all must be able to reopen them."""
    registry = get_registry()

    for row in default_breakers():
        row.breaker._state = CircuitState.OPEN

    count = await registry.reset_all()

    assert count >= len(DECLARED_NAMES)
    for row in default_breakers():
        assert row.breaker.state == CircuitState.CLOSED, f"{row.name} was not reset"


def test_webhook_scope_bypass_still_returns_the_global():
    """The lazy re-export must preserve identity, not hand back a copy."""
    assert get_webhook_breaker(None) is webhook_circuit


def test_a_bare_breaker_is_not_silently_registered():
    """The invariant is 'declared defaults are visible', not 'everything is'.

    Auto-registering every CircuitBreaker would break tests that construct
    their own, and would make the default table meaningless.
    """
    from src.app.core.utils.circuit_breaker import CircuitBreaker

    CircuitBreaker(name="undeclared_ad_hoc_breaker")
    assert get_registry().get("undeclared_ad_hoc_breaker") is None
