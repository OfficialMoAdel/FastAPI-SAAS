"""The tenant-model seam: core resolves tenants from a registered row type.

``core.tenancy`` needs the tenant row to answer a slug lookup, and importing
the tenants feature model would be a ``core -> feature`` edge the H-5 contract
forbids. So the model is registered from ``src.app.models`` — the single
model-registration point both processes already import — and core asks for it
through :mod:`core.tenancy.models`.

These pin the three failure modes that matter: core asking before anything
registered, the boot gate not firing, and a test reset leaking into production.
"""

from __future__ import annotations

import pytest

from src.app.core.tenancy import models as tenant_models


@pytest.fixture
def unregistered():
    """Unwire the seam, then restore whatever was there."""
    saved = tenant_models._tenant_model
    tenant_models.reset_tenant_model()
    yield
    tenant_models._tenant_model = saved


def test_require_raises_when_unwired(unregistered):
    """Core asking for the row type before registration fails loudly."""
    with pytest.raises(RuntimeError, match="tenant model is not registered"):
        tenant_models.require_tenant_model()


def test_boot_gate_raises_when_unwired(unregistered):
    """The boot gate exists so the failure lands at startup, not on first lookup."""
    with pytest.raises(RuntimeError, match="tenant model is not registered"):
        tenant_models.ensure_tenant_model_registered()


def test_application_seam_registers_the_model():
    """Importing src.app.models is what supplies the row type."""
    import src.app.models  # noqa: F401 — the import is the registration

    model = tenant_models.require_tenant_model()
    assert model.__name__ == "Tenant"
    tenant_models.ensure_tenant_model_registered()


def test_registered_model_serves_a_real_query():
    """The registered type is the ORM class, not a stand-in."""
    from src.app.modules.tenants.models import Tenant

    assert tenant_models.require_tenant_model() is Tenant


def test_reset_is_blocked_in_production(monkeypatch):
    """A production reset would fail the next request instead of the next boot."""
    from src.app.core.config import EnvironmentOption

    monkeypatch.setattr(tenant_models.settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION)
    with pytest.raises(RuntimeError, match="test-only and blocked in production"):
        tenant_models.reset_tenant_model()


def test_core_tenancy_does_not_import_the_feature_model():
    """The edge this seam exists to remove must not creep back."""
    import ast
    from pathlib import Path

    root = Path("src/app/core/tenancy")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.app.modules"), (
                    f"{path} imports {node.module}; the tenant model comes from "
                    "core.tenancy.models instead"
                )
