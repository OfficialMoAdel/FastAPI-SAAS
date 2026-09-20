"""Authentication database operations and CRUD classes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastcrud import FastCRUD
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.tenant_crud import TenantAwareCRUD, scoped_query, tenant_scope
from src.app.modules.auth.models import (
    MagicLink,
    MFAMethod,
    PasskeyCredential,
    SSOSettings,
    UserSSOConnection,
)
from src.app.modules.auth.schemas import (
    PasskeyCredentialCreate,
    PasskeyCredentialRead,
    PasskeyCredentialUpdate,
    SSOSettingsCreateInternal,
    SSOSettingsDelete,
    SSOSettingsRead,
    SSOSettingsUpdateInternal,
    UserSSOConnectionCreate,
    UserSSOConnectionRead,
    UserSSOConnectionUpdate,
)

# ============================================================================
# SSO Settings CRUD
# ============================================================================


class CRUDSSOSettings(
    FastCRUD[
        SSOSettings,
        SSOSettingsCreateInternal,
        SSOSettingsUpdateInternal,
        SSOSettingsUpdateInternal,
        SSOSettingsDelete,
        SSOSettingsRead,
    ]
):
    pass


crud_sso_settings = CRUDSSOSettings(SSOSettings)


# ============================================================================
# User SSO Connection CRUD
# ============================================================================


class CRUDUserSSOConnection(
    TenantAwareCRUD[
        UserSSOConnection,
        UserSSOConnectionCreate,
        UserSSOConnectionUpdate,
        UserSSOConnectionUpdate,
        UserSSOConnectionUpdate,
        UserSSOConnectionRead,
    ]
):
    async def get_by_criteria(
        self,
        db: AsyncSession,
        *,
        id: int | None = None,
        user_id: int | None = None,
        provider: str | None = None,
        provider_user_id: str | None = None,
        tenant_id: UUID | None = None,
    ) -> UserSSOConnection | None:
        """Get SSO connection by various criteria."""
        query = scoped_query(UserSSOConnection, tenant_id)
        if id is not None:
            query = query.where(UserSSOConnection.id == id)
        if user_id is not None:
            query = query.where(UserSSOConnection.user_id == user_id)
        if provider is not None:
            query = query.where(UserSSOConnection.provider == provider)
        if provider_user_id is not None:
            query = query.where(UserSSOConnection.provider_user_id == provider_user_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()
    async def get_by_provider_user_id(
        self,
        db: AsyncSession,
        provider: str,
        provider_user_id: str,
        tenant_id: UUID | None = None,
    ) -> UserSSOConnection | None:
        """Global lookup for SSO login by unique provider ID."""
        stmt = select(UserSSOConnection).where(
            UserSSOConnection.provider == provider,
            UserSSOConnection.provider_user_id == provider_user_id,
        )
        if tenant_id is not None:
            stmt = stmt.where(tenant_scope(UserSSOConnection, tenant_id))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    async def exists_for_user(
        self,
        db: AsyncSession,
        user_id: int,
        provider: str,
        tenant_id: UUID | None = None,
    ) -> bool:
        """Check if SSO connection exists for user and provider."""
        query = (
            select(func.count(UserSSOConnection.id))
            .where(
                UserSSOConnection.user_id == user_id,
                UserSSOConnection.provider == provider,
            )
            .where(tenant_scope(UserSSOConnection, tenant_id))
        )
        result = await db.execute(query)
        count = result.scalar()
        return (count or 0) > 0

    async def count_for_user(
        self,
        db: AsyncSession,
        user_id: int | None = None,
        tenant_id: UUID | None = None,
        id: int | None = None,
        **kwargs: Any,
    ) -> int:
        """Count SSO connections for a user."""
        if id is not None:
            return await super().count(db=db, id=id, **kwargs)  # type: ignore[no-any-return]
        query = select(func.count(UserSSOConnection.id))
        if user_id is not None:
            query = query.where(UserSSOConnection.user_id == user_id)
        query = query.where(tenant_scope(UserSSOConnection, tenant_id))
        result = await db.execute(query)
        return result.scalar() or 0
    async def delete_by_criteria(
        self,
        db: AsyncSession,
        *,
        id: int | None = None,
        user_id: int | None = None,
        provider: str | None = None,
        tenant_id: UUID | None = None,
    ) -> None:
        """Delete SSO connection by ID or by user/provider combo."""
        if id is not None and user_id is None and provider is None:
            await super().delete(db=db, id=id)
            return
        conn = await self.get_by_criteria(db, id=id, user_id=user_id, provider=provider, tenant_id=tenant_id)
        if conn:
            await db.delete(conn)
            await db.commit()

crud_user_sso_connections = CRUDUserSSOConnection(UserSSOConnection)


# ============================================================================
# Passkey CRUD
# ============================================================================


class CRUDPasskeyCredential(
    FastCRUD[
        PasskeyCredential,
        PasskeyCredentialCreate,
        PasskeyCredentialUpdate,
        PasskeyCredentialUpdate,
        Any,
        PasskeyCredentialRead,
    ]
):
    """CRUD operations for passkey credentials."""

    MAX_PASSKEYS_PER_USER = 10

    async def get_credential(
        self,
        db: AsyncSession,
        *,
        id: int | None = None,
        credential_id: bytes | None = None,
        credential_id_hash: str | None = None,
        user_id: int | None = None,
    ) -> PasskeyCredential | None:
        """Get passkey credential by ID, credential_id, or credential_id_hash."""
        query = select(PasskeyCredential)
        if id is not None:
            query = query.where(PasskeyCredential.id == id)
        elif credential_id_hash is not None:
            query = query.where(PasskeyCredential.credential_id_hash == credential_id_hash)
        elif credential_id is not None:
            query = query.where(PasskeyCredential.credential_id == credential_id)

        if user_id is not None:
            query = query.where(PasskeyCredential.user_id == user_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()
    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> list[PasskeyCredential]:
        """Get all passkey credentials for a user."""
        query = (
            select(PasskeyCredential)
            .where(PasskeyCredential.user_id == user_id)
            .order_by(PasskeyCredential.created_at.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def count_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> int:
        """Count passkey credentials for a user."""
        query = select(func.count(PasskeyCredential.id)).where(PasskeyCredential.user_id == user_id)
        result = await db.execute(query)
        return result.scalar() or 0

    async def can_create(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> bool:
        """Check if user can create another passkey (under limit)."""
        count = await self.count_by_user(db, user_id)
        return count < self.MAX_PASSKEYS_PER_USER

    async def delete_credential(
        self,
        db: AsyncSession,
        credential_id: int | None = None,
        user_id: int | None = None,
        db_object: PasskeyCredential | None = None,
    ) -> None:
        """Delete a passkey credential."""
        if db_object is not None:
            await db.delete(db_object)
            await db.commit()
            return
        query = select(PasskeyCredential)
        if credential_id is not None:
            query = query.where(PasskeyCredential.id == credential_id)
        if user_id is not None:
            query = query.where(PasskeyCredential.user_id == user_id)
        result = await db.execute(query)
        cred = result.scalar_one_or_none()
        if cred:
            await db.delete(cred)
            await db.commit()


crud_passkey_credentials = CRUDPasskeyCredential(PasskeyCredential)



# ============================================================================
# Magic Link CRUD
# ============================================================================


def _hash_token(token: str) -> str:
    """Hash a magic link token (I2: delegated to the peppered seam)."""
    from src.app.core.security.tokens import hash_token as _ht

    return _ht(token)


async def create_magic_link(
    db: AsyncSession,
    *,
    user_id: int,
    token: str,
    action: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    expires_in_minutes: int = 15,
) -> MagicLink:
    """Create a magic link record in the database."""
    expires_at = datetime.now(UTC) + timedelta(minutes=expires_in_minutes)
    hashed_token = _hash_token(token)

    magic_link = MagicLink(
        user_id=user_id,
        token=hashed_token,
        action=action,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(magic_link)
    await db.commit()
    await db.refresh(magic_link)
    return magic_link


async def get_magic_link_by_token(
    db: AsyncSession,
    token: str,
) -> MagicLink | None:
    """Get a magic link by token.

    I2 bridge: the stored digest changed from bare SHA-256 to peppered HMAC,
    so links issued before that change are invisible to a single-digest
    lookup. Query both; a legacy hit is re-hashed so the row migrates
    forward on first use (subsequent lookups hit the current digest).
    """
    from src.app.core.security.tokens import token_digest_candidates

    current_digest, legacy_digest = token_digest_candidates(token)
    stmt = select(MagicLink).where(MagicLink.token.in_((current_digest, legacy_digest)))
    result = await db.execute(stmt)
    magic_link = result.scalar_one_or_none()

    if magic_link is not None and magic_link.token == legacy_digest:
        magic_link.token = current_digest
        await db.commit()
        await db.refresh(magic_link)
    return magic_link


async def mark_magic_link_used(
    db: AsyncSession,
    magic_link: MagicLink,
) -> MagicLink:
    """Mark a magic link as used.

    R1-3: conditional UPDATE — claims the row only when ``used_at IS NULL``.
    Zero affected rows means a concurrent verify won the race, so raise
    instead of silently double-issuing sessions.
    """
    from src.app.modules.auth.errors import MagicLinkAlreadyUsedError

    stmt = (
        update(MagicLink)
        .where(MagicLink.id == magic_link.id, MagicLink.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise MagicLinkAlreadyUsedError("Token already used")
    await db.commit()
    await db.refresh(magic_link)
    return magic_link


async def is_token_expired(magic_link: MagicLink) -> bool:
    """Check if a magic link token has expired."""
    return datetime.now(UTC) > magic_link.expires_at


async def is_token_used(magic_link: MagicLink) -> bool:
    """Check if a magic link token has been used."""
    return magic_link.used_at is not None


# ============================================================================
# MFA CRUD
# ============================================================================

crud_mfa_methods: FastCRUD[MFAMethod, None, None, None, None, None] = FastCRUD(MFAMethod)  # type: ignore[type-var]


__all__ = [
    "CRUDSSOSettings",
    "crud_sso_settings",
    "CRUDUserSSOConnection",
    "crud_user_sso_connections",
    "CRUDPasskeyCredential",
    "crud_passkey_credentials",
    "create_magic_link",
    "get_magic_link_by_token",
    "mark_magic_link_used",
    "is_token_expired",
    "is_token_used",
    "crud_mfa_methods",
]
