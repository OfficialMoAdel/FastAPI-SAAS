"""The permission-graph seam: core joins four row types the app registers.

``core.rbac`` resolves a user's permissions across UserRole -> Role ->
RolePermission -> Permission. Importing the rbac feature module would be a
``core -> feature`` edge the H-5 contract forbids, so the four row types are
registered as one set from ``src.app.models``.

These pin the failure modes: core asking before registration, the boot gate
not firing, a production reset, and — the reason the set is registered as a
unit rather than four hooks — a partial graph.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.app.core.rbac import models as rbac_models


@pytest.fixture
def unregistered():
    """Unwire the seam, then restore whatever was there."""
    saved = rbac_models._permission_graph
    rbac_models.reset_permission_graph()
    yield
    rbac_models._permission_graph = saved


def test_require_raises_when_unwired(unregistered):
    with pytest.raises(RuntimeError, match="permission graph is not registered"):
        rbac_models.require_permission_graph()


def test_boot_gate_raises_when_unwired(unregistered):
    """The gate exists so the failure lands at startup, not on first lookup."""
    with pytest.raises(RuntimeError, match="permission graph is not registered"):
        rbac_models.ensure_permission_graph_registered()


def test_application_seam_registers_the_real_models():
    """Importing src.app.models supplies the four actual ORM classes."""
    import src.app.models  # noqa: F401 — the import is the registration
    from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole

    graph = rbac_models.require_permission_graph()
    assert graph.user_role is UserRole
    assert graph.role is Role
    assert graph.role_permission is RolePermission
    assert graph.permission is Permission
    rbac_models.ensure_permission_graph_registered()


def test_the_graph_is_registered_and_read_as_one_unit():
    """Four models, one registration, and no half-updated state.

    Registering the four separately would allow a partial graph that fails
    deep inside the join. The unit is structural: the graph is frozen and
    takes all four at construction, so it cannot be half-filled after the
    fact, and there is exactly one registration call.
    """
    import dataclasses
    import inspect

    fields = [f.name for f in dataclasses.fields(rbac_models.PermissionGraph)]
    assert fields == ["user_role", "role", "role_permission", "permission"]
    assert all(f.type is not None for f in dataclasses.fields(rbac_models.PermissionGraph))

    with pytest.raises(dataclasses.FrozenInstanceError):
        graph = rbac_models.require_permission_graph()
        graph.user_role = object  # type: ignore[misc]

    params = list(inspect.signature(rbac_models.register_permission_graph).parameters)
    assert params == ["graph"], "the four types arrive in one registration, not four"


def test_reset_is_blocked_in_production(monkeypatch):
    from src.app.core.config import EnvironmentOption

    monkeypatch.setattr(rbac_models.settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION)
    with pytest.raises(RuntimeError, match="test-only and blocked in production"):
        rbac_models.reset_permission_graph()


def test_core_rbac_does_not_import_the_feature_model():
    """The edge this seam exists to remove must not creep back."""
    for path in Path("src/app/core/rbac").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.app.modules"), (
                    f"{path} imports {node.module}; the row types come from "
                    "core.rbac.models instead"
                )
