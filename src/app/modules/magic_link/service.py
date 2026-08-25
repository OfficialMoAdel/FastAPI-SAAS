"""Magic Link authentication service.

Business logic for magic link passwordless authentication.
Handles token generation, email queueing, and token verification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.app.core.security.pending_token_store import PendingTokenStore

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.security.tokens import get_magic_link_manager
from src.app.core.utils.cache import async_get_redis
from src.app.modules.auth.schemas import LoginMFAResponse, LoginTokenResponse
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.modules.magic_link.exceptions import (
    InvalidMagicLinkError,
    MagicLinkUserInactiveError,
)
from src.app.modules.magic_link.schemas import MagicLinkSendResponse
from src.app.modules.users.crud import crud_users

logger = get_logger(__name__)


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

        # Check if user is active (check both deleted AND active status)
        # BUG FIX: Previously only checked `is_deleted`. This inconsistency with
        # verify_magic_link (which checks both) caused tokens to be created and emails
        # sent for inactive users, which would then be rejected on verification.
        if user.get("is_deleted", False) or not user.get("is_active", False):
            logger.debug(
                "Magic link requested for deleted/inactive user",
                extra={"operation": "send_magic_link", "user_id": user["id"]},
            )
            return MagicLinkSendResponse(
                message="If an account exists, a magic link has been sent",
                may_be_delayed=False,
            )

        # Generate and store token in Redis
        manager = self._get_magic_link_manager()
        from src.app.core.security.tokens import TokenGenerator
        token = TokenGenerator.generate_magic_link_token()
        await manager.create(
            token=token,
            data={
                "email": email,
                "action": "login",
                "extra_data": {
                    "user_id": user["id"],
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                }
            }
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
        except Exception as e:
            logger.error("Failed to store magic link in DB", exc_info=True)
            from src.app.modules._base.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError("Magic link setup is currently unavailable.") from e

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

    async def verify_magic_link(
        self,
        token: str,
        ip_address: str,
        response: Any = None,
    ) -> LoginTokenResponse | LoginMFAResponse:
        """Verify a magic link token and return auth tokens.

        Args:
            token: The magic link token from the email.
            response: Optional FastAPI Response object to set cookies.

        Returns:
            LoginTokenResponse with access token, or
            LoginMFAResponse if MFA is required.

        Raises:
            InvalidMagicLinkError: If token is invalid or expired.
            MagicLinkAlreadyUsedError: If token was already used.
            MagicLinkUserInactiveError: If user account is inactive.
        """
        manager = self._get_magic_link_manager()
        # Step 1: Read token data WITHOUT consuming (peek)
        token_data = await manager.peek(token)

        if token_data is None:
            # Token not found - check if it was already used
            from src.app.modules.magic_link.crud import get_magic_link_by_token
            from src.app.modules.magic_link.exceptions import MagicLinkAlreadyUsedError

            db_link = await get_magic_link_by_token(db=self.db, token=token)
            if db_link and db_link.used_at is not None:
                raise MagicLinkAlreadyUsedError("Token already used")

            logger.warning(
                "Magic link validation failed",
                extra={"operation": "verify_magic_link", "reason": "token_not_found"},
            )
            raise InvalidMagicLinkError("Token expired or invalid")

        # Step 2: Update audit trail in database immediately BEFORE consuming Redis token
        from src.app.modules.magic_link.crud import get_magic_link_by_token, mark_magic_link_used

        try:
            db_link = await get_magic_link_by_token(db=self.db, token=token)
            if db_link:
                await mark_magic_link_used(db=self.db, magic_link=db_link)
        except Exception:
            logger.error("Failed to mark magic link used in DB", exc_info=True)
            # DB failure → do NOT consume Redis token, let user retry
            raise InvalidMagicLinkError("Temporary error, please try again")

        # Step 3: NOW consume the Redis token (safe — DB is marked)
        consumed_data = await manager.consume(token)
        if consumed_data is None:
            raise MagicLinkAlreadyUsedError("Token already used or expired")

        email = consumed_data.get("email")
        if not email:
            raise InvalidMagicLinkError("Invalid token data")

        # M8: prefer the user_id bound at link creation; email-only lookup is
        # only a fallback for legacy unbound links. Prevents an email change
        # between send and verify from resolving a different account.
        extra_data = consumed_data.get("extra_data") or {}
        bound_user_id = extra_data.get("user_id")
        if bound_user_id is not None:
            user = await crud_users.get_user_with_tier_eager(db=self.db, id=int(bound_user_id))
            if user is None:
                raise InvalidMagicLinkError("User not found")
            # Tenant isolation: get_user_with_tier_eager already filters by the
            # request's tenant context; an empty result above means cross-tenant.
            if str(user.get("email", "")).lower() != str(email).lower():
                logger.warning(
                    "Magic link email mismatch for bound user",
                    extra={"operation": "verify_magic_link", "user_id": bound_user_id},
                )
                raise InvalidMagicLinkError("User not found")
        else:
            # Legacy unbound link: resolve by email only
            user = await self._get_user_by_email(email)
            if user is None:
                raise InvalidMagicLinkError("User not found")

        # Check if user is active
        if user.get("is_deleted", False) or not user.get("is_active", False):
            raise MagicLinkUserInactiveError("User account is inactive")

        # Check MFA
        from src.app.modules.mfa.service import MFAService

        mfa_service = MFAService()
        mfa_enabled = await mfa_service.is_mfa_enabled(user["id"], self.db)

        if mfa_enabled:
            from src.app.core.security import get_mfa_pending_token_manager
            from src.app.core.security.tokens import TokenGenerator

            # Generate MFA pending token
            mfa_token_store = get_mfa_pending_token_manager(self.redis)
            mfa_token = TokenGenerator.generate_magic_link_token()
            await mfa_token_store.create(
                token=mfa_token,
                data={
                    "user_id": user["id"],
                    "username": str(user.get("username") or user.get("email") or ""),
                    "ip_address": ip_address,
                    "scope": "mfa_pending",
                }
            )

            logger.info(
                "Magic link verified, MFA required",
                extra={
                    "operation": "verify_magic_link",
                    "user_id": user["id"],
                },
            )

            return LoginMFAResponse(
                mfa_required=True,
                mfa_token=mfa_token,
                expires_in=int(settings.MFA_PENDING_EXPIRY_SECONDS),
                message="MFA verification required",
            )

        # Create auth tokens
        tokens = await self._create_auth_tokens(user)

        # Set cookie if response is provided
        refresh_token = tokens.pop("refresh_token", None)
        if response and refresh_token:
            from src.app.core.security.login_helpers import set_refresh_cookie
            set_refresh_cookie(response, refresh_token)

        logger.info(
            "Magic link verified successfully",
            extra={
                "operation": "verify_magic_link",
                "user_id": user["id"],
            },
        )

        return LoginTokenResponse(**tokens)

    def _get_magic_link_manager(self) -> PendingTokenStore:
        """Get PendingTokenStore instance.

        Returns:
            PendingTokenStore with Redis client.
        """
        return get_magic_link_manager(redis=self.redis)

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
        from urllib.parse import quote

        from src.app.core.utils import queue

        # Build magic link URL. URL-encode the token to guard against any special
        # characters in future token formats and prevent URL truncation or injection.
        magic_link_url = f"{settings.FRONTEND_URL}/auth/magic-link?token={quote(token, safe='')}"

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
        """Create access and refresh tokens and register the session.

        Delegates to the shared TokenIssuer seam; the "Magic Link Login"
        device label applies because this flow has no HTTP request context.

        Args:
            user: User dict with tier eager-loaded.

        Returns:
            Dict with access_token, refresh_token, token_type, expires_in.
        """
        issued = await TokenIssuer.issue_session(
            user,
            channel="magic_link",
        )
        return {
            "access_token": issued.access_token,
            "refresh_token": issued.refresh_token,
            "token_type": "bearer",
            "expires_in": issued.expires_in,
        }


async def get_magic_link_service(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> MagicLinkService:
    """Factory function for dependency injection.

    Args:
        db: Async database session.
        redis: Async Redis client.

    Returns:
        MagicLinkService instance.
    """
    return MagicLinkService(db=db, redis=redis)
