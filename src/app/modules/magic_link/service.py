"""Magic Link authentication service.

Business logic for magic link passwordless authentication.
Handles token generation, email queueing, and token verification.
"""


from __future__ import annotations

import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
)
from src.app.core.security.tokens import MagicLinkManager
from src.app.modules.auth.schemas import AuthTokenResponse
from src.app.modules.magic_link.exceptions import (
    InvalidMagicLinkError,
    MagicLinkUserInactiveError,
)
from src.app.modules.magic_link.schemas import MagicLinkSendResponse
from src.app.modules.users.crud import crud_users

logger = logging.getLogger(__name__)


class MagicLinkService:
    """Service for magic link passwordless authentication.

    Handles the complete magic link flow:
    1. Generating and storing tokens (Redis for speed, DB for audit)
    2. Queueing magic link emails for delivery
    3. Validating tokens and returning auth tokens

    Security features:
    - One-time use tokens (consumed on first use)
    - 15-minute token expiration
    - No email enumeration (returns success even for non-existent users)
    - IP and user-agent logging for audit
    """

    def __init__(self, db: AsyncSession, redis: Redis) -> None:
        """Initialize the magic link service.

        Args:
            db: Async database session.
            redis: Async Redis client.
        """
        self.db = db
        self.redis = redis

    async def send_magic_link(
        self,
        email: str,
        ip_address: str,
        user_agent: str,
    ) -> MagicLinkSendResponse:
        """Send a magic link to the specified email.

        Security: Always returns success response to prevent email enumeration.

        Args:
            email: Target email address.
            ip_address: Request IP for audit logging.
            user_agent: Request user agent for audit logging.

        Returns:
            MagicLinkSendResponse with status message.
        """
        # Look up user by email
        user = await self._get_user_by_email(email)

        if user is None:
            # Return success to prevent email enumeration
            logger.debug(
                "Magic link requested for non-existent email",
                extra={"operation": "send_magic_link", "email_found": False},
            )
            return MagicLinkSendResponse(
                message="If an account exists, a magic link has been sent",
                may_be_delayed=False,
            )

        # Check if user is active
        if user.get("is_deleted", False):
            logger.debug(
                "Magic link requested for deleted user",
                extra={"operation": "send_magic_link", "user_id": user["id"]},
            )
            return MagicLinkSendResponse(
                message="If an account exists, a magic link has been sent",
                may_be_delayed=False,
            )

        # Generate and store token in Redis
        manager = self._get_magic_link_manager()
        token = await manager.create_token(
            email=email,
            action="login",
            extra_data={
                "user_id": user["id"],
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )

        # Store in database for audit trail with hashed token (H-14)
        from src.app.modules.magic_link.crud import create_magic_link
        try:
            await create_magic_link(
                db=self.db,
                user_id=user["id"],
                token=token,
                action="login",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception:
            logger.error("Failed to store magic link in DB", exc_info=True)

        # Queue email for delivery (async task)
        await self._queue_magic_link_email(
            email=email,
            token=token,
            user_id=user["id"],
        )

        logger.info(
            "Magic link created and email queued",
            extra={
                "operation": "send_magic_link",
                "user_id": user["id"],
            },
        )

        return MagicLinkSendResponse(
            message="If an account exists, a magic link has been sent",
            may_be_delayed=False,
        )

    async def verify_magic_link(self, token: str) -> AuthTokenResponse:
        """Verify a magic link token and return auth tokens.

        Args:
            token: The magic link token from the email.

        Returns:
            AuthTokenResponse with access and refresh tokens.

        Raises:
            InvalidMagicLinkError: If token is invalid or expired.
            MagicLinkAlreadyUsedError: If token was already used.
            MagicLinkUserInactiveError: If user account is inactive.
        """
        # Validate token in Redis (one-time use)
        manager = self._get_magic_link_manager()
        token_data = await manager.validate_token(token)

        if token_data is None:
            # Token not found - could be expired or already used
            # Check if we can distinguish (for 410 vs 400)
            logger.warning(
                "Magic link validation failed",
                extra={"operation": "verify_magic_link", "reason": "token_not_found"},
            )
            raise InvalidMagicLinkError("Token expired or invalid")

        email = token_data.get("email")
        if not email:
            raise InvalidMagicLinkError("Invalid token data")

        # Get user by email
        user = await self._get_user_by_email(email)
        if user is None:
            raise InvalidMagicLinkError("User not found")

        # Check if user is active
        if user.get("is_deleted", False):
            raise MagicLinkUserInactiveError("User account is inactive")

        # Create auth tokens
        tokens = await self._create_auth_tokens(user)

        # Update audit trail in database
        from src.app.modules.magic_link.crud import get_magic_link_by_token, mark_magic_link_used
        try:
            db_link = await get_magic_link_by_token(db=self.db, token=token)
            if db_link:
                await mark_magic_link_used(db=self.db, magic_link=db_link)
        except Exception:
            logger.error("Failed to mark magic link used in DB", exc_info=True)

        logger.info(
            "Magic link verified successfully",
            extra={
                "operation": "verify_magic_link",
                "user_id": user["id"],
            },
        )

        return AuthTokenResponse(**tokens)

    def _get_magic_link_manager(self) -> MagicLinkManager:
        """Get MagicLinkManager instance.

        Returns:
            MagicLinkManager with Redis client.
        """
        return MagicLinkManager(redis=self.redis)

    async def _get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Get user by email address with tier eager-loaded.

        Args:
            email: User email address.

        Returns:
            User dict with tier if found, None otherwise.
        """
        result = await crud_users.get_user_with_tier_eager(db=self.db, email=email)
        return result

    async def _queue_magic_link_email(
        self,
        email: str,
        token: str,
        user_id: int,
    ) -> None:
        """Queue magic link email for delivery.

        Uses ARQ task queue for async email delivery with retry.

        Args:
            email: Target email address.
            token: Magic link token.
            user_id: User ID for logging.
        """
        from src.app.core.utils import queue

        # Build magic link URL
        magic_link_url = f"{settings.FRONTEND_URL}/auth/magic-link?token={token}"

        # Enqueue via ARQ if available
        if queue.pool:
            try:
                await queue.pool.enqueue_job(
                    "send_magic_link_email_task",
                    email,
                    magic_link_url,
                    user_id,
                )
                logger.info(
                    "magic_link_email_enqueued",
                    extra={"user_id": user_id, "email": email},
                )
            except Exception as e:
                logger.error(
                    "failed_to_enqueue_magic_link_email",
                    extra={"user_id": user_id, "email": email, "error": str(e)},
                )
                # Fallback: log that the link was created but not enqueued
                logger.warning(
                    "magic_link_fallback_no_queue",
                    extra={"user_id": user_id, "email": email},
                )
        else:
            # ARQ not available - log that email wasn't sent
            logger.warning(
                "arq_queue_not_available_magic_link",
                extra={"user_id": user_id, "email": email},
            )

    async def _create_auth_tokens(self, user: dict) -> dict[str, Any]:
        """Create access and refresh tokens for user with tier info for rate limiting.

        Args:
            user: User dict with tier eager-loaded.

        Returns:
            Dict with access_token, refresh_token, token_type, expires_in.
        """
        # Include tier info in JWT claims for Zero-DB rate limiting
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        token_data = {
            "sub": user["username"],
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
        }

        # Create tokens
        access_token = await create_access_token(data=token_data)
        refresh_token = await create_refresh_token(data=token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }


async def get_magic_link_service(db: AsyncSession, redis: Redis) -> MagicLinkService:
    """Factory function for dependency injection.

    Args:
        db: Async database session.
        redis: Async Redis client.

    Returns:
        MagicLinkService instance.
    """
    return MagicLinkService(db=db, redis=redis)
