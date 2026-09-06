"""Storage repository protocol."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class StorageRepository(Protocol):
    """Protocol for file metadata and tenant storage settings persistence."""

    async def get_file(self, tenant_id: UUID, file_id: int) -> Any | None:
        """Fetch file metadata by tenant and file ID."""
        ...

    async def get_settings(self, tenant_id: UUID) -> Any | None:
        """Fetch tenant storage settings."""
        ...

    async def save_file(self, file_data: Any) -> Any:
        """Save a new file record."""
        ...

    async def update_settings(self, tenant_id: UUID, settings_data: Any) -> Any:
        """Create or update storage settings for a tenant."""
        ...

    async def delete_file(self, tenant_id: UUID, file_id: int) -> bool:
        """Delete a file metadata record."""
        ...

    async def get_quota_usage(self, tenant_id: UUID) -> dict[str, int]:
        """Get current storage bytes and file count for a tenant."""
        ...


__all__ = ["StorageRepository"]
