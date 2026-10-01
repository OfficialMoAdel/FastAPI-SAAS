"""Every boot gate must fail at boot, not at first request.

A startup gate exists to turn a missing registration into a boot failure. A
gate that does not is the failure mode this project keeps meeting twice over:
`.importlinter` sat inert because stale ignore entries aborted it before any
edge was evaluated, and the ARQ worker once ran on an import side effect
(`core/worker/settings.py` doing `from src.app.models import *`) rather than on
a called gate.

So each gate is unwired here and the resulting failure is observed. A gate test
that never observes a failure proves nothing, which is why the negative cases
are the substance of this file and the positive ones are the counter-test.

Coverage of the gate table is enforced by
`scripts/check_boot_gate_coverage.py`, which parses every gate in src/app
and fails on one missing from this file's table, and prints the count on
every CI run.

Each gate's message is also checked for the name of what is missing. "Service
unavailable" is not actionable; "rate policy fetcher is not registered. Import
src.app.models" tells an operator what to do at 3am.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest


@dataclass(frozen=True)
class Gate:
    """One boot gate, and the module globals that wire it."""

    name: str
    module: str
    func: str
    seams: tuple[str, ...]
    error: str
    must_name: str
    truthy: Any = object()


# Every `ensure_*_registered` / boot-phase gate in src/app, and the
# counterpart `require_*` accessor where one exists.
GATES: tuple[Gate, ...] = (
    Gate(
        name="principal_provider",
        module="src.app.core.principal",
        func="ensure_principal_provider_registered",
        seams=("_principal_provider",),
        error="ServiceUnavailableError",
        must_name="principal provider",
    ),
    Gate(
        name="jwt_lookups",
        module="src.app.core.security.jwt_service",
        func="ensure_jwt_lookups_registered",
        seams=("_token_version_lookup", "_user_tenant_lookup"),
        error="ServiceUnavailableError",
        must_name="token_version",
    ),
    Gate(
        name="tenant_model",
        module="src.app.core.tenancy.models",
        func="ensure_tenant_model_registered",
        seams=("_tenant_model",),
        error="RuntimeError",
        must_name="tenant model",
    ),
    Gate(
        name="permission_graph",
        module="src.app.core.rbac.models",
        func="ensure_permission_graph_registered",
        seams=("_permission_graph",),
        error="RuntimeError",
        must_name="permission graph",
    ),
    Gate(
        name="rate_policy_fetcher",
        module="src.app.core.security.policies",
        func="ensure_rate_policy_fetcher_registered",
        seams=("_rate_policy_fetcher",),
        error="RuntimeError",
        must_name="rate policy fetcher",
    ),
    Gate(
        name="bypass_recorder",
        module="src.app.core.worker.audit",
        func="ensure_bypass_recorder_registered",
        seams=("_bypass_recorder",),
        error="RuntimeError",
        must_name="bypass recorder",
    ),
    Gate(
        name="feature_tasks",
        module="src.app.core.worker.registry",
        func="ensure_feature_tasks_registered",
        seams=("_feature_tasks",),
        error="RuntimeError",
        must_name="feature background tasks",
    ),
    Gate(
        name="feature_collaborators",
        module="src.app.core.worker.registry",
        func="ensure_feature_collaborators_registered",
        seams=("_feature_collaborators",),
        error="RuntimeError",
        must_name="feature collaborators",
    ),
)


def _gate(gate: Gate) -> Callable[[], None]:
    return getattr(importlib.import_module(gate.module), gate.func)


def _unwire(gate: Gate) -> dict[str, Any]:
    """Patch every seam of `gate` to the unwired value.

    `_feature_tasks` is a collection whose emptiness is the unwired state
    rather than `None`, so it gets an empty container.
    """
    module = importlib.import_module(gate.module)
    unwired: dict[str, Any] = {}
    for seam in gate.seams:
        empty = [] if "tasks" in seam else None
        unwired[seam] = patch.object(module, seam, empty)
        unwired[seam].start()
    return unwired


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.name)
def test_gate_fails_when_unwired(gate: Gate) -> None:
    """The decisive assertion: unwire it, and the gate must raise.

    Without this, a gate that silently returned would look identical to a
    working one from every test that boots the app.
    """
    patches = _unwire(gate)
    try:
        with pytest.raises(Exception) as exc_info:
            _gate(gate)()
    finally:
        for p in patches.values():
            p.stop()

    assert type(exc_info.value).__name__ == gate.error, (
        f"{gate.name} raised {type(exc_info.value).__name__}, expected {gate.error}"
    )


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.name)
def test_failure_message_names_what_is_missing(gate: Gate) -> None:
    """A generic message is not actionable at 3am.

    The message must name the registration that is absent, so the operator
    knows which import or wiring step to restore.
    """
    patches = _unwire(gate)
    try:
        with pytest.raises(Exception) as exc_info:
            _gate(gate)()
    finally:
        for p in patches.values():
            p.stop()

    message = str(exc_info.value).lower()
    assert gate.must_name in message, (
        f"{gate.name} message does not name the missing registration: {exc_info.value!r}"
    )


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.name)
def test_gate_passes_when_wired(gate: Gate) -> None:
    """COUNTER-TEST: a wired seam must not raise.

    Without this, a gate that raised unconditionally would pass every failure
    test above while making the app unbootable. The gate must test a
    condition, not merely fire.
    """
    module = importlib.import_module(gate.module)
    wired = {seam: patch.object(module, seam, gate.truthy) for seam in gate.seams}
    for p in wired.values():
        p.start()
    try:
        _gate(gate)()  # must not raise
    finally:
        for p in wired.values():
            p.stop()


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.name)
def test_only_the_unwired_seam_decides(gate: Gate) -> None:
    """Each seam independently arms the gate.

    `ensure_jwt_lookups_registered` guards two lookups; a test that unwires
    both passes even if the gate only ever checked one.
    """
    module = importlib.import_module(gate.module)
    for seam in gate.seams:
        empty: Any = [] if "tasks" in seam else None
        with patch.object(module, seam, empty):
            for other in gate.seams:
                if other != seam:
                    patcher = patch.object(module, other, gate.truthy)
                    patcher.start()
            try:
                with pytest.raises(Exception):
                    _gate(gate)()
            finally:
                for other in gate.seams:
                    if other != seam:
                        patcher = patch.object(module, other, gate.truthy)
                        patcher.stop()
