from datetime import UTC, datetime

from fastcrud import FastCRUD
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.schemas import TokenBlacklistCreate, TokenBlacklistRead, TokenBlacklistUpdate


class CRUDTokenBlacklist(
    FastCRUD[
        TokenBlacklist,
        TokenBlacklistCreate,
        TokenBlacklistUpdate,
        TokenBlacklistUpdate,
        TokenBlacklistUpdate,
        TokenBlacklistRead,
    ]
):
    """Extended CRUD for token blacklist with cache support."""

    async def get_active_tokens(self, db: AsyncSession) -> list[tuple[str, datetime]]:
        """Get all non-expired blacklisted tokens for cache warming.

        Returns:
            List of (token, expires_at) tuples for active tokens
        """
        # DB column is likely naive timestamp (UTC) in current schema.
        # We strip timezone from 'now' to allow comparison with naive columns.
        # SECURITY: This ensures compatibility across different DB configurations.
        now = datetime.now(UTC).replace(tzinfo=None)
        stmt = select(TokenBlacklist.token, TokenBlacklist.expires_at).where(TokenBlacklist.expires_at > now)
        result = await db.execute(stmt)
        return [(row.token, row.expires_at) for row in result.all()]


crud_token_blacklist = CRUDTokenBlacklist(TokenBlacklist)
