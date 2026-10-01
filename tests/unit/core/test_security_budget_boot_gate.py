"""The security-budget boot gate must fail at boot, not at first request.

`composition.py::_start_security_budget_gate` is the only startup phase in the
boot-gate matrix (`tests/unit/core/test_boot_gates.py`) with no test. It calls
`validate_budget_definitions()`, which exists precisely to convert a
misconfiguration into a boot failure: leg budget names come from a config
table, so an operator who removes `mfa_verify_global` from `SECURITY_BUDGETS`
would otherwise hit a `KeyError` on the first MFA verification in production.

The gate checks in BOTH directions:
  - a leg naming a budget that is not configured  -> boot must fail
  - a budget whose declared fail-closed posture disagrees with
    `SECURITY_BUDGETS_FAIL_CLOSED`               -> boot must fail

The second direction is the one that is easy to lose: it fails OPEN on a Redis
outage, which for a brute-force control is a silent security downgrade that
boots clean and only misbehaves under attack.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app.composition import _start_security_budget_gate
from src.app.core.security.budgets import BUDGETS


def _configured_budgets() -> set[str]:
    from src.app.core.config import settings

    return set(settings.rate_limit.SECURITY_BUDGETS)


def _fail_closed_budgets() -> set[str]:
    from src.app.core.config import settings

    return set(settings.rate_limit.SECURITY_BUDGETS_FAIL_CLOSED)


def _every_referenced_budget() -> set[str]:
    return {leg.budget for definition in BUDGETS.values() for leg in definition.legs}


async def test_gate_passes_on_a_correctly_configured_system() -> None:
    """COUNTER-TEST: a healthy boot emits no error.

    Without this, a gate that always raises is indistinguishable from a gate
    that works, and operators would disable it.
    """
    await _start_security_budget_gate()


def test_every_referenced_budget_is_configured() -> None:
    """The shipped config covers every leg the code references."""
    missing = sorted(_every_referenced_budget() - _configured_budgets())
    assert not missing, f"budgets referenced by BUDGETS but not configured: {missing}"


async def test_unconfigured_budget_fails_the_gate() -> None:
    """Remove one budget from config -> boot fails, naming the leg.

    This is the exact scenario the gate was written for: without it the
    failure is a KeyError on the first request that hits that leg.
    """
    target = sorted(_every_referenced_budget())[0]
    reduced = _configured_budgets() - {target}

    with patch("src.app.core.security.budgets.settings.rate_limit.SECURITY_BUDGETS", reduced):
        with pytest.raises(RuntimeError) as exc:
            await _start_security_budget_gate()

    message = str(exc.value)
    assert "unconfigured budgets" in message
    assert target in message, f"the error must name the missing budget, got: {message}"


async def test_fail_closed_posture_mismatch_fails_the_gate() -> None:
    """A budget that should fail closed but is absent from the list -> boot fails.

    The silent-downgrade case. Left unchecked this boots clean and then fails
    OPEN under a Redis outage, disabling a brute-force control exactly when an
    attacker can exploit it.
    """
    definition = next(d for d in BUDGETS.values() if d.fail_closed)
    leg = definition.legs[0]

    with patch(
        "src.app.core.security.budgets.settings.rate_limit.SECURITY_BUDGETS_FAIL_CLOSED",
        _fail_closed_budgets() - {leg.budget},
    ):
        with pytest.raises(RuntimeError) as exc:
            await _start_security_budget_gate()

    message = str(exc.value)
    assert "fail_closed" in message or "SECURITY_BUDGETS_FAIL_CLOSED" in message
    assert leg.budget in message, f"the error must name the mis-declared budget: {message}"


async def test_gate_raises_through_the_phase_not_just_the_function() -> None:
    """The failure propagates out of the phase, so the boot actually aborts.

    A gate that logs loudly and returns is not a gate — that is the failure
    mode this repo has already been bitten by twice (a stale ignore_imports
    entry that aborted the import-linter contract before any edge was
    evaluated, and an ARQ worker surviving on an import side effect instead of
    a called gate).
    """
    with patch(
        "src.app.core.security.budgets.validate_budget_definitions",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await _start_security_budget_gate()
