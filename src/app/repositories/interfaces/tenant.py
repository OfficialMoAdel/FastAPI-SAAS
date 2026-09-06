"""Tenant repository protocol."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class TenantRepository(Protocol):
    """Protocol for tenant data persistence and queries."""

    async def get(self, tenant_id: UUID | str) -> dict[str, Any] | None:
        """Fetch a single tenant by UUID."""
        ...

    async def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Fetch a single tenant by slug."""
        ...

    async def get_multi(
        self,
        *,
        offset: int = 0,
        limit: int = 10,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        """Fetch paginated tenants with total count."""
        ...

    async def create(self, tenant_data: Any) -> dict[str, Any]:
        """Create a new tenant record."""
        ...

    async def update(self, tenant_id: UUID | str, tenant_data: Any) -> dict[str, Any] | None:
        """Update an existing tenant record."""
        ...

    async def delete(self, tenant_id: UUID | str) -> bool:
        """Delete a tenant record."""
        ...


__all__ = ["TenantRepository"]
