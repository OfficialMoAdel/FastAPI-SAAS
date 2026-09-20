"""Password lifecycle — change and reset flows plus session/cache invalidation.

Extracted from ``AuthService`` so the password change-axes (verification,
complexity enforcement, token-version invalidation, reset-token handling)
live in one unit. Behavior is identical to the original inline implementation.
"""

import asyncio
import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.security import get_password_hash, verify_password
from src.app.core.security.tokens import hash_token
from src.app.modules.audit_logs import AuditGuarantee, audit
from src.app.modules.auth.models import PasswordResetToken, generate_reset_token
from src.app.modules.users import UserService
from src.app.modules.users.session_invalidation import InvalidationReason
from src.app.shared.exceptions import UnauthorizedError, ValidationError

logger = get_logger(__name__)


class PasswordLifecycleService:
    """Owns password change/reset workflows and their session invalidation side effects."""

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Change user password.

        SECURITY FIX: Invalidates all user sessions on password change.
        This ensures that if an account is compromised, changing the password
        immediately terminates all active sessions.
        """
        # SECURITY FIX: Only active users can change their password
        # Ensure we properly fallback to False when checking active status in cached payload manually
        user_service = UserService(db)
        user = await user_service.get_active_user(id=user_id)
        if not user:
            raise UnauthorizedError("User not found.")

        # Verify current password
        if not await verify_password(current_password, user["hashed_password"]):
            raise UnauthorizedError("Current password is incorrect.")

        # SECURITY FIX: Enforce password complexity
        try:
            from src.app.modules.users.schemas import UserCreate

            UserCreate.validate_password_complexity(new_password)
        except ValueError as e:
            raise ValidationError(str(e))

        # Hash and update new password
        new_hashed = await get_password_hash(new_password)
        await user_service.crud.update(db=db, id=user_id, object={"hashed_password": new_hashed}, commit=False)

        # 1B: the seam owns the SQL bump + commit + cache in one place, so the
        # password change and the session invalidation commit atomically and
        # the SQL bump happens exactly once. Cache is written post-commit
        # inside the seam to prevent re-caching the old DB value.
        tenant_id = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
        await user_service.invalidate_sessions(
            user,
            InvalidationReason.CREDENTIAL_CHANGE,
            tenant_id=tenant_id,
            commit=True,
        )

        await self.delete_user_token_cache(user)
        # SECURITY FIX: Add audit log for password change (H-13)
        await audit(
            action="auth.password_changed",
            entity_type="user",
            entity_id=user_id,
            tenant_id=user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None),
            user_id=user_id,
            details={"message": "User password was changed"},
            guarantee=AuditGuarantee.BEST_EFFORT,
        )

        return {"message": "Password changed successfully. All other sessions have been logged out."}

    async def invalidate_all_user_sessions(self, user: dict[str, Any], db: AsyncSession) -> None:
        """Increment token_version in DB to invalidate all active sessions for a user.

        This implementation uses token_versioning for instant global invalidation.
        When token_version is incremented in the DB, all previously issued tokens
        (which contain the old version) will be rejected by verify_token().

        1B: the SQL bump is owned by the ``invalidate_user_sessions`` seam so
        it happens exactly once and stays tenant-scoped.

        NOTE: Cache invalidation happens inside the seam. Callers that need
        the cache written AFTER their own commit should call the seam (via
        ``UserService.invalidate_sessions``) with ``commit=True`` directly
        instead of this no-commit helper.
        """
        user_service = UserService(db)
        tenant_id = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
        await user_service.invalidate_sessions(
            user,
            InvalidationReason.CREDENTIAL_CHANGE,
            tenant_id=tenant_id,
            commit=False,
        )
        # Note: We do NOT commit here to allow callers to manage their own transactions (e.g. change_password)

        logger.info("invalidated_all_user_sessions_in_db", user_id=user.get("id"))

    async def delete_user_token_cache(self, user: dict[str, Any]) -> None:
        """Delete token_version cache keys for a user.

        Should be called AFTER DB commit to ensure next reads get the new version.
        """
        user_id = user.get("id")

        # Invalidate all active Redis sessions
        try:
            from src.app.core.security.session_manager import session_manager as global_session_manager

            if user_id is not None:
                await global_session_manager.invalidate_all_sessions(str(user_id))
        except Exception as e:
            logger.error("failed_to_invalidate_redis_sessions", user_id=user_id, error=str(e))

        try:
            from src.app.core.redis_provider import get_redis

            redis_client = get_redis()

            if redis_client:
                username = user.get("username")
                email = user.get("email")
                tenant_id = user.get("tenant_id")
                keys = []
                from src.app.core.utils.cache import build_tenant_key

                if username:
                    keys.append(build_tenant_key(f"user:{username}:token_version", tenant_id))
                if email:
                    keys.append(build_tenant_key(f"user:{email}:token_version", tenant_id))
                if keys:
                    # Fix P0.2: Forcefully overwrite the cache with the new token version
                    # to prevent stale-read race conditions that could allow old tokens.
                    new_version = user.get("token_version", 0) + 1
                    async with redis_client.pipeline() as pipe:
                        for key in keys:
                            pipe.set(key, str(new_version), ex=300)
                        await pipe.execute()
                    logger.debug("overwrote_user_token_cache", user_id=user_id, keys=keys)
        except Exception as e:
            logger.warning("Failed to delete token_version cache", user_id=user_id, error=str(e))

    async def request_password_reset(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Request password reset (sends email with reset token).

        Always returns success to prevent email enumeration.
        """
        # BUG FIX: Also require is_active=True to prevent inactive accounts from
        # resetting their password without going through account reactivation.
        user_service = UserService(db)
        user = await user_service.get_active_user(email=email)

        # S5 FIX: Block password reset for SSO-only accounts
        if user:
            hashed_pw = (
                user.get("hashed_password", "") if isinstance(user, dict) else getattr(user, "hashed_password", "")
            )
            if str(hashed_pw).startswith("!SSO_ONLY"):
                # Treat SSO-only accounts same as non-existent (timing equalized below)
                user = None

        # Always return success to prevent email enumeration
        if user:
            raw_token = generate_reset_token()
            hashed_token = hash_token(raw_token)
            # Create password reset token (expires in 1 hour)
            reset_token = PasswordResetToken(user_id=user["id"], token=hashed_token, tenant_id=user["tenant_id"])
            db.add(reset_token)
            await db.commit()
            await db.refresh(reset_token)

            logger.info("Password reset token created", user_id=user["id"], token_id=reset_token.id)

            reset_link = f"{settings.app.FRONTEND_URL}/reset-password?token={raw_token}"
            # BUG FIX: Previously published directly to "notification.send" with keys
            # `template_name` and `context`. The only subscriber requires `notification_id`,
            # `subject`, and `html_content` (pre-rendered). The mismatched payload caused the
            # event to be silently dropped → password reset emails were NEVER sent.
            # Standardized notification dispatch via outbox seam
            from src.app.core.events.notifications import request_notification

            await request_notification(
                to_email=email,
                template_name="password_reset",
                context={
                    "reset_link": reset_link,
                    "username": user["username"],
                },
                tenant_id=str(user["tenant_id"]) if user.get("tenant_id") else None,
                user_id=user["id"],
            )
        else:
            # SECURITY FIX: Equalize timing for enumeration prevention
            raw_token = generate_reset_token()
            hashed_token = hash_token(raw_token)

            try:
                user_service = UserService(db)
                await user_service.crud.get(db=db, id=-1)
            except Exception:
                pass

            # SECURITY FIX: Equalize timing for enumeration prevention.
            # NOTE: Do NOT publish a real notification event here — the dummy path
            # previously published "notification.request" to dummy@example.com,
            # creating outbound-send attempts/log noise on every enumeration probe.
            # A pure async delay matching the real branch's cost is sufficient.
            await asyncio.sleep(random.uniform(0.1, 0.2))

        return {"message": "If an account exists with that email, a reset link has been sent."}

    async def reset_password(
        self,
        token: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Reset password using reset token.

        SECURITY FIX: Explicitly checks if user account is deleted or inactive
        before allowing password reset. Prevents reactivation of deleted accounts.
        """
        # SECURITY FIX: Use SELECT FOR UPDATE to prevent concurrent token reuse.
        # Without this lock, two concurrent requests with the same token could both
        # pass the is_valid check before either marks the token as used.
        # I2 bridge: the stored digest changed from bare SHA-256 to peppered
        # HMAC, so reset tokens issued before that change are invisible to a
        # single-digest lookup. Lock-matching rows under BOTH digests, then
        # re-hash a legacy hit so the row migrates forward on first use.
        from src.app.core.security.tokens import token_digest_candidates

        current_digest, legacy_digest = token_digest_candidates(token)
        stmt = (
            select(PasswordResetToken)
            .where(PasswordResetToken.token.in_((current_digest, legacy_digest)))
            .with_for_update()
        )
        result = await db.execute(stmt)
        reset_token = result.scalar_one_or_none()

        if reset_token is not None and reset_token.token == legacy_digest:
            reset_token.token = current_digest
            await db.commit()

        if not reset_token:
            raise UnauthorizedError("Invalid password reset token.")

        if not reset_token.is_valid:
            if reset_token.used:
                raise UnauthorizedError("This password reset token has already been used.")
            if reset_token.is_expired:
                raise UnauthorizedError("This password reset token has expired.")
            raise UnauthorizedError("Invalid password reset token.")

        # SECURITY FIX: Get user WITHOUT is_deleted filter to check account status
        user_service = UserService(db)
        user = await user_service.crud.get(db=db, id=reset_token.user_id)
        if not user:
            raise UnauthorizedError("Invalid password reset token.")

        # S5 FIX: Prevent SSO-only accounts from setting a local password
        hashed_pw = user.get("hashed_password", "") if isinstance(user, dict) else getattr(user, "hashed_password", "")
        if str(hashed_pw).startswith("!SSO_ONLY"):
            raise UnauthorizedError("This account uses SSO authentication. Password reset is not available.")

        # SECURITY FIX: Use generic message for deleted/inactive accounts
        # Prevents account enumeration attacks - attackers cannot determine account status
        is_deleted = user.get("is_deleted", False) if isinstance(user, dict) else getattr(user, "is_deleted", False)
        is_active = user.get("is_active", False) if isinstance(user, dict) else getattr(user, "is_active", False)
        if is_deleted or not is_active:
            await asyncio.sleep(0.15 + random.uniform(-0.05, 0.05))
            raise UnauthorizedError("This account is not accessible. Please contact support for assistance.")

        # SECURITY FIX: Enforce password complexity BEFORE marking token as used
        try:
            from src.app.modules.users.schemas import UserCreate

            UserCreate.validate_password_complexity(new_password)
        except ValueError as e:
            raise ValidationError(str(e))

        # Mark token as used AFTER successful validation
        # SECURITY FIX: Prevents token reuse if password change fails later, but avoids burning on bad input
        reset_token.used = True
        await db.flush()  # Ensure token is marked as used before proceeding

        # Update password
        new_hashed = await get_password_hash(new_password)
        await user_service.crud.update(
            db=db, id=reset_token.user_id, object={"hashed_password": new_hashed}, commit=False
        )

        # 1B: the seam owns the SQL bump + commit + cache, so the token-use
        # marking, password update, and session invalidation commit atomically
        # and the SQL bump happens exactly once. Password reset is a
        # CREDENTIAL_CHANGE; the bump forces re-auth of every active session
        # and prevents the lost-password attacker from reusing a captured JWT.
        tenant_id = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
        await user_service.invalidate_sessions(
            user,
            InvalidationReason.CREDENTIAL_CHANGE,
            tenant_id=tenant_id,
            commit=True,
        )
        await self.delete_user_token_cache(user)

        logger.info("Password reset successful", user_id=reset_token.user_id, token_id=reset_token.id)

        return {"message": "Password reset successfully. Please log in with your new password."}
