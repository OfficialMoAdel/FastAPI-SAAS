"""Supported storage provider names.

The vendor vocabulary of the outbound storage seam. It lives here — a leaf
module with no project imports — so both sides of the seam can name a provider
without importing each other:

- ``src.app.clients.storage`` (registry, factories) keys its factory map on it.
- ``src.app.modules.storage.models`` re-exports it for the ``StorageSettings``
  column, so the outbound side never imports the feature side for a name.

Kept dependency-free on purpose. See ``src/app/clients/storage/__init__.py``
for why the outbound package must not eagerly import the vendor adapters.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["StorageProviderType"]


class StorageProviderType(str, Enum):
    """Supported storage provider types."""

    S3 = "s3"
    AZURE = "azure"
    LOCAL = "local"
