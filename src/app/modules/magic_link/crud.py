"""Magic Link CRUD operations.

Database operations for magic link token records.
Token validation uses Redis for performance (MagicLinkManager);
this provides audit trail persistence.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MagicLink


def _hash_token(token: str) -> str:
    """Hash a magic link token using SHA-256 for secure DB storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    """Create a magic link record in the database.

    Args:
        db: Async database session.
        user_id: User ID for the magic link.
        token: Generated secure token.
        action: Token action type (login, verify_email, password_reset).
        ip_address: Request IP address for audit.
        user_agent: Request user agent for audit.
        expires_in_minutes: Token expiration time in minutes.

    Returns:
        Created MagicLink record.
    """
    expires_at = datetime.now(UTC) + timedelta(minutes=expires_in_minutes)

    magic_link = MagicLink(
        user_id=user_id,
        token=_hash_token(token),
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

    Args:
        db: Async database session.
        token: Magic link token.

    Returns:
        MagicLink if found, None otherwise.
    """
    stmt = select(MagicLink).where(MagicLink.token == _hash_token(token))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def mark_magic_link_used(
    db: AsyncSession,
    magic_link: MagicLink,
) -> MagicLink:
    """Mark a magic link as used.

    Args:
        db: Async database session.
        magic_link: MagicLink record to update.

    Returns:
        Updated MagicLink record.
    """
    magic_link.used_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(magic_link)
    return magic_link


async def is_token_expired(magic_link: MagicLink) -> bool:
    """Check if a magic link token has expired.

    Args:
        magic_link: MagicLink record to check.

    Returns:
        True if expired, False otherwise.
    """
    return datetime.now(UTC) > magic_link.expires_at


async def is_token_used(magic_link: MagicLink) -> bool:
    """Check if a magic link token has been used.

    Args:
        magic_link: MagicLink record to check.

    Returns:
        True if used, False otherwise.
    """
    return magic_link.used_at is not None
