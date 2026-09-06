"""Tenant context resolution and cross-tenant mismatch guards for billing services."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.app.core.tenancy.context import get_current_tenant
from src.app.shared.exceptions import ModelNotFoundError


def resolve_billing_tenant_id(
    explicit_tenant_id: int | UUID | str | None = None,
    entity_name: str = "Billing",
    entity_id: Any = None,
    tenant_getter: Any = None,
) -> Any:
    """Resolve tenant ID with cross-tenant mismatch guard.

    Ensures that if an explicit tenant_id is provided, it matches the active
    request tenant context; otherwise falls back to the active context.
    Raises ModelNotFoundError if no tenant can be resolved or a mismatch occurs.
    """
    ctx = tenant_getter() if tenant_getter is not None else get_current_tenant()
    ctx_id = ctx.id if ctx is not None else None
    if explicit_tenant_id is not None and ctx_id is not None and str(explicit_tenant_id) != str(ctx_id):
        raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
    tid = explicit_tenant_id if explicit_tenant_id is not None else ctx_id
    if tid is None:
        raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
    return tid


__all__ = ["resolve_billing_tenant_id"]
