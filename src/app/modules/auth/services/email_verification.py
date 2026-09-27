"""Email verification — send and confirm verification-token flows.

Extracted from ``AuthService`` so the email-verification change-axis lives
in one unit. Behavior is identical to the original inline implementation,
including timing equalization to prevent account enumeration.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.security.tokens import hash_token
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.auth.models import EmailVerificationToken, generate_verification_token
from src.app.modules.users import UserDirectory, UserService
from src.app.modules.users.session_invalidation import InvalidationReason
from src.app.shared.exceptions import UnauthorizedError


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
        from src.app.shared.exceptions import ModelNotFoundError

        directory = UserDirectory(db)
        try:
            found = await directory.by_email(email)
        except ModelNotFoundError:
            found = None
        user = found.model_dump(mode="json") if found is not None else None

        is_active = bool(get_user_attr(user, "is_active", False))
        is_email_verified = bool(get_user_attr(user, "is_email_verified", False))

        # If inactive and ALREADY verified, it means admin disabled the account. Do not send email.
        if user and not is_active and is_email_verified:
            user = None

        if user and not is_email_verified:
            from uuid import UUID

            user_id = get_user_attr(user, "id")
            user_tenant = get_user_attr(user, "tenant_id")
            user_name = get_user_attr(user, "username")

            tenant_id_val: UUID = user_tenant if isinstance(user_tenant, UUID) else UUID(str(user_tenant))

            raw_token = generate_verification_token()
            hashed_token = hash_token(raw_token)
            # Create verification token
            token_model = EmailVerificationToken(user_id=user_id, token=hashed_token, tenant_id=tenant_id_val)
            db.add(token_model)
            await db.commit()
            await db.refresh(token_model)

            verification_link = f"{settings.app.FRONTEND_URL}/verify-email?token={raw_token}"

            # Standardized notification dispatch via event bus outbox seam
            from src.app.core.events.notifications import request_notification

            await request_notification(
                to_email=email,
                template_name="email_verification",
                context={
                    "username": user_name,
                    "verification_link": verification_link,
                },
                tenant_id=user_tenant,
                user_id=user_id,
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
        # I2 bridge: the stored digest changed from bare SHA-256 to peppered
        # HMAC, so tokens issued before that change are invisible to a
        # single-digest lookup. Query both, then re-hash a legacy hit so the
        # row migrates forward on its first use.
        from src.app.core.security.tokens import token_digest_candidates

        current_digest, legacy_digest = token_digest_candidates(token)
        # Find token in database (locked: prevents concurrent verification use)
        stmt = (
            select(EmailVerificationToken)
            .where(EmailVerificationToken.token.in_((current_digest, legacy_digest)))
            .with_for_update()
        )
        result = await db.execute(stmt)
        verification_token = result.scalar_one_or_none()

        if verification_token is not None and verification_token.token == legacy_digest:
            # Run-3: this used to commit inside the FOR UPDATE region, releasing
            # the row lock before the single-use marker (the row delete below) was
            # committed, allowing a double verification under concurrency. The
            # re-hash now flushes into the same transaction.
            verification_token.token = current_digest
            await db.flush()

        if not verification_token:
            raise UnauthorizedError("Invalid verification token.")

        if verification_token.is_expired:
            raise UnauthorizedError("Verification token has expired.")

        # Get user
        user_service = UserService(db)
        directory = UserDirectory(db)
        found = await directory.by_id(verification_token.user_id)
        if found is None:
            raise UnauthorizedError("User not found.")
        user = found.model_dump(mode="json")

        # BUG FIX: Reject verification for admin-disabled accounts.
        # But if they are inactive ONLY because they are unverified, we activate them here.
        is_active = bool(get_user_attr(user, "is_active", False))
        is_email_verified = bool(get_user_attr(user, "is_email_verified", False))
        if not is_active and is_email_verified:
            # Already verified but inactive -> Admin disabled
            raise UnauthorizedError("This account is inactive. Please contact support.")

        # SANCTIONED WRITE: UserService.update requires ownership + schema.
        # Update user status to verified and active
        await user_service.crud.update(
            db=db, id=verification_token.user_id, object={"is_email_verified": True, "is_active": True}, commit=False
        )

        # Delete token (it's single use)
        await db.delete(verification_token)

        # 1B: CREDENTIAL_CHANGE owns the SQL bump via the seam, so the user
        # update, token deletion, and session invalidation commit atomically
        # and the bump happens exactly once with the fresh version passed
        # through to the cache.
        await user_service.invalidate_sessions(
            user,
            InvalidationReason.CREDENTIAL_CHANGE,
            tenant_id=get_user_attr(user, "tenant_id"),
            commit=True,
        )
        return {"message": "Email verified successfully."}


def make_email_verifier(db: AsyncSession) -> Callable[[str], Awaitable[None]]:
    """Build an email-verifier closure bound to the live request session.

    Shared factory for auth ``register`` + users ``write_user`` (dedupes the
    identical inline closures). ``db`` must be the live request session —
    never defer the returned callable into background work.
    """

    async def _verifier(email: str) -> None:
        await EmailVerificationService().send_verification_email(email, db)

    return _verifier
