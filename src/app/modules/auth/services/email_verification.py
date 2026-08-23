"""Email verification — send and confirm verification-token flows.

Extracted from ``AuthService`` so the email-verification change-axis lives
in one unit. Behavior is identical to the original inline implementation,
including timing equalization to prevent account enumeration.
"""

import asyncio
import random
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.core.security.tokens import hash_token
from src.app.core.service_registry import get_service
from src.app.modules.auth.models import EmailVerificationToken, generate_verification_token

logger = get_logger(__name__)


class EmailVerificationService:
    """Owns the send/confirm email-verification workflows."""

    async def send_verification_email(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Send email verification link.

        Always returns success to prevent email enumeration.
        """

        # BUG FIX: Allow unverified (inactive) accounts to request verification.
        # But prevent admin-disabled (inactive AND verified) accounts from abusing this.
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, email=email, is_deleted=False)

        is_active = user.get("is_active", False) if isinstance(user, dict) else getattr(user, "is_active", False)
        is_email_verified = (
            user.get("is_email_verified", False)
            if isinstance(user, dict)
            else getattr(user, "is_email_verified", False)
        )

        # If inactive and ALREADY verified, it means admin disabled the account. Do not send email.
        if user and not is_active and is_email_verified:
            user = None

        if user and not is_email_verified:
            raw_token = generate_verification_token()
            hashed_token = hash_token(raw_token)
            # Create verification token
            token_model = EmailVerificationToken(user_id=user["id"], token=hashed_token, tenant_id=user["tenant_id"])
            db.add(token_model)
            await db.commit()
            await db.refresh(token_model)

            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={raw_token}"

            # BUG FIX: Use NotificationService for correctly shaped events
            from src.app.modules.notifications.service import NotificationService

            notification_service = NotificationService(db)
            await notification_service.send_email(
                tenant_id=UUID(str(user["tenant_id"])) if user.get("tenant_id") else None,
                to_email=email,
                template_name="email_verification",
                context={
                    "username": user["username"],
                    "verification_link": verification_link,
                },
                user_id=user["id"],
            )
        else:
            # SECURITY FIX: Equalize timing for enumeration prevention
            # Simulate DB writes and event bus publishing latency
            # Added jitter to prevent precise timing analysis
            await asyncio.sleep(0.15 + random.uniform(-0.05, 0.05))

        return {"message": "If the email is registered and unverified, a verification link has been sent."}

    async def verify_email(
        self,
        token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Verify email using verification token."""
        hashed_token = hash_token(token)
        # Find token in database
        stmt = select(EmailVerificationToken).where(EmailVerificationToken.token == hashed_token).with_for_update()
        result = await db.execute(stmt)
        verification_token = result.scalar_one_or_none()

        if not verification_token:
            raise UnauthorizedException("Invalid verification token.")

        if verification_token.is_expired:
            raise UnauthorizedException("Verification token has expired.")

        # Get user
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, id=verification_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # BUG FIX: Reject verification for admin-disabled accounts.
        # But if they are inactive ONLY because they are unverified, we activate them here.
        is_active = user.get("is_active", False) if isinstance(user, dict) else getattr(user, "is_active", False)
        is_email_verified = (
            user.get("is_email_verified", False)
            if isinstance(user, dict)
            else getattr(user, "is_email_verified", False)
        )
        if not is_active and is_email_verified:
            # Already verified but inactive -> Admin disabled
            raise UnauthorizedException("This account is inactive. Please contact support.")

        # Update user status to verified and active
        await user_service.crud.update(
            db=db, id=verification_token.user_id, object={"is_email_verified": True, "is_active": True}, commit=False
        )

        # Delete token (it's single use)
        await db.delete(verification_token)
        await db.commit()

        # Invalidate auth cache AFTER commit
        user_service = get_service("user", db)
        await user_service._invalidate_user_cache(user)

        return {"message": "Email verified successfully."}
