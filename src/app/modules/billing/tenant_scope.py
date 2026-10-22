"""Shared tenant-scoping base for the billing services (F8).

The billing module has five services that each own a database session and
an optional construction-time tenant id: ``StripeSettingsService``,
``ProductService``, ``PricingService`` (in ``service.py``) and
``SubscriptionPlanService``, ``UserSubscriptionService`` (in
``subscription_service.py``).

Each of them used to carry its own copy of the same two accessors:

- ``current_tenant_id`` — a read-only view over the tenant seam
- ``_resolve_tenant`` — the fail-closed resolution used before a write

The copies were byte-identical apart from the default ``entity_name``,
which is why they drifted: the three in ``service.py`` each documented the
others as "the ONE seam" while repeating the boilerplate. This module
holds that boilerplate once.

The default ``entity_name`` is a class attribute, so a subclass overrides
it rather than re-implementing the method::

    class ProductService(TenantScopedService):
        _default_entity_name = "Product"
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.tenancy.context import resolve_tenant_id


class TenantScopedService:
    """Mixin: the ONE tenant-resolution seam for a billing service.

    Attributes
    ----------
    db : AsyncSession
        The request-scoped database session.
    tenant_id : int | UUID | None
        The construction-time tenant id, for worker/system paths that
        have no bound tenant context.
    """

    #: The entity name used by ``_resolve_tenant`` when the caller does
    #: not pass one. Subclasses override it with their own entity.
    _default_entity_name: str = "Billing"

    db: AsyncSession
    tenant_id: int | UUID | None

    @property
    def current_tenant_id(self) -> Any:
        """Effective tenant id — read-only view over the ONE seam.

        Returns the context tenant when bound, else the construction value,
        else ``None`` (``fail_closed=False``). Callers that must not proceed
        without a tenant use :meth:`_resolve_tenant` instead.
        """
        return resolve_tenant_id(
            self.tenant_id,
            entity_name="Tenant",
            entity_id="current",
            fail_closed=False,
        )

    def _resolve_tenant(self, entity_name: str | None = None, entity_id: Any = None) -> Any:
        """Resolve the tenant for a scoped read or write.

        The context tenant always wins; a mismatching construction value
        soft-404s. With neither bound, this fails closed (the seam's
        default), so a service can never operate without a scope.
        """
        return resolve_tenant_id(
            self.tenant_id,
            entity_name=entity_name or self._default_entity_name,
            entity_id=entity_id,
        )
