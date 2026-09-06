"""Composition root: wires repository/client concretes to domain seams.

Centralizes construction of the repository and integration-client
implementations introduced in Phases 3-4 so routers and services depend on
one wiring point instead of instantiating concretes inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.app.clients.billing.stripe import PaymentGateway

from src.app.core.db.database import async_get_db
from src.app.repositories.interfaces.billing import BillingRepository
from src.app.repositories.interfaces.storage import StorageRepository
from src.app.repositories.interfaces.tenant import TenantRepository
from src.app.repositories.interfaces.user import UserRepository
from src.app.repositories.sqlalchemy.billing import SqlAlchemyBillingRepository
from src.app.repositories.sqlalchemy.storage import SqlAlchemyStorageRepository
from src.app.repositories.sqlalchemy.tenant import SqlAlchemyTenantRepository
from src.app.repositories.sqlalchemy.user import SqlAlchemyUserRepository


async def get_user_repository(db: AsyncSession = Depends(async_get_db)) -> UserRepository:
    """Provide the UserRepository implementation for the request scope."""
    return SqlAlchemyUserRepository(db)


async def get_tenant_repository(db: AsyncSession = Depends(async_get_db)) -> TenantRepository:
    """Provide the TenantRepository implementation for the request scope."""
    return SqlAlchemyTenantRepository(db)


async def get_storage_repository(db: AsyncSession = Depends(async_get_db)) -> StorageRepository:
    """Provide the StorageRepository implementation for the request scope."""
    return SqlAlchemyStorageRepository(db)


async def get_billing_repository(db: AsyncSession = Depends(async_get_db)) -> BillingRepository:
    """Provide the BillingRepository implementation for the request scope."""
    return SqlAlchemyBillingRepository(db)


def get_payment_gateway_client(api_key: str | None = None) -> PaymentGateway:
    """Provide the payment gateway client with an explicitly resolved API key."""
    from src.app.clients.billing.stripe import get_payment_gateway

    return get_payment_gateway(api_key=api_key)


__all__ = [
    "get_billing_repository",
    "get_payment_gateway_client",
    "get_storage_repository",
    "get_tenant_repository",
    "get_user_repository",
]
