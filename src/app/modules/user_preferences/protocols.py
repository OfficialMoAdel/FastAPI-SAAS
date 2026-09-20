"""Narrow structural seams for user_preferences cross-module needs (I8).

The preferences service previously imported ``storage.service`` directly
(bypassing the storage facade) to acquire a provider. It now depends only
on this protocol; acquisition happens at the edge (lazy facade resolution
or test injection), keeping the module's import surface facade-compliant.
"""

from typing import BinaryIO, Protocol

from src.app.modules.storage import StorageResult


class ProfileImageStore(Protocol):
    """The slice of StorageProvider that profile-image handling needs."""

    async def upload(self, file: BinaryIO, path: str, mime_type: str) -> StorageResult: ...

    async def delete(self, path: str) -> StorageResult: ...

    async def exists(self, path: str) -> bool: ...

    async def get_url(self, path: str, expires_in: int = 3600) -> str: ...


__all__ = ["ProfileImageStore"]
