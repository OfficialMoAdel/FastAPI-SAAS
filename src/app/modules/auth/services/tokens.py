"""Token issuance — JWT claim building and access/refresh token creation.

Extracted from ``AuthService`` so token mechanics (claims shape, parallel
token generation) live in one unit, separate from the login workflow.
Behavior is identical to the original inline implementation.
"""

import asyncio
from datetime import timedelta
from typing import Any

from src.app.core.config import settings
from src.app.core.security import create_access_token, create_refresh_token


class TokenIssuer:
    """Builds JWT claims for authenticated users and issues token pairs."""

    @staticmethod
    def build_token_data(subject: str, user: dict[str, Any]) -> dict[str, Any]:
        """Build the JWT claim payload shared by login and token refresh.

        Includes tier info for Zero-DB rate limiting, ``token_version`` for
        session invalidation on password change, and ``tenant_id`` for tenant
        context validation.
        """
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        return {
            "sub": subject,
            "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
            "token_version": user.get("token_version", 0),
        }

    @staticmethod
    async def create_token_pair(token_data: dict[str, Any]) -> tuple[str, str]:
        """Create an access/refresh token pair in parallel.

        Returns
        -------
        tuple[str, str]
            ``(access_token, refresh_token)``
        """
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        return await asyncio.gather(
            create_access_token(data=token_data, expires_delta=access_token_expires),
            create_refresh_token(data=token_data),
        )
