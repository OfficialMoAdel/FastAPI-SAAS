"""Auth service layer - encapsulates all authentication business logic."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import Request, Response
from jwt import ExpiredSignatureError
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import EnvironmentOption, settings
from src.app.core.events import EventPriority, event_bus
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    MFA_PENDING_EXPIRY_SECONDS,
    MFAPendingTokenManager,
    TokenType,
    blacklist_token,
    blacklist_tokens,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
    verify_token,
)
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules.auth.models import EmailVerificationToken, PasswordResetToken
from src.app.modules.mfa.service import MFAService
from src.app.modules.users.crud import crud_users
from src.app.modules.users.models import User
from src.app.modules.users.service import get_user_service

logger = get_logger(__name__)


def get_auth_service() -> "AuthService":
    """Factory function to get AuthService instance.

    Returns
    -------
    AuthService
        Singleton-like auth service instance.
    """
    return AuthService()


class AuthService:
    """Service class for authentication-related business logic."""

    # Precomputed dummy bcrypt hash for timing attack prevention.
    # Used when user doesn't exist to ensure constant-time authentication.
    _DUMMY_BCRYPT_HASH = "$2b$12$E9xqo1Z2bUoF8q1Qn8lFmu9Vz7Z3kQs5r2wEe7yHj3kLmNpQrStu"  # noqa: S105

    # Atomic Lua script for login attempt checking.
    # Combines lockout check + increment + expire into single Redis roundtrip.
    # Returns -1 if locked out, otherwise the new attempt count.
    _LOGIN_CHECK_LUA = """
local attempts = redis.call('GET', KEYS[1])
if attempts and tonumber(attempts) >= tonumber(ARGV[1]) then
    return -1
end
local new_attempts = redis.call('INCR', KEYS[1])
if new_attempts == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return new_attempts
"""

    async def authenticate_user(self, username_or_email: str, password: str, db: AsyncSession) -> dict[str, Any] | None:
        """Authenticate user by checking database and password.

        Uses constant-time authentication to prevent timing attacks.
        Always performs bcrypt verification regardless of whether user exists.

        Returns
        -------
        dict[str, Any] | None
            User data dict if authentication successful, None otherwise.
        """
        # Use eager loading to get tier info for JWT claims (enables Zero-DB rate limiting)
        # Use UserService to benefit from auth info caching
        user_service = get_user_service(db)
        if "@" in username_or_email:
            db_user = await user_service.get_user_auth_info(email=username_or_email)
        else:
            db_user = await user_service.get_user_auth_info(username=username_or_email)

        # SECURITY: Use dummy hash if user not found to prevent timing attacks.
        # This ensures bcrypt verification always takes ~100ms, preventing
        # attackers from enumerating valid usernames via response time analysis.
        hash_to_verify = db_user["hashed_password"] if db_user else self._DUMMY_BCRYPT_HASH
        password_valid = await verify_password(password, hash_to_verify)

        # Only return user data if both user exists AND password is valid
        if not db_user or not password_valid:
            return None

        return db_user

    async def _publish_login_audit(
        self,
        request: Request,
        username: str,
        success: bool,
        tenant_id: int | UUID | None = None,
        user_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        """Publish audit log for login attempt."""
        try:
            if not tenant_id:
                tenant = get_tenant_context()
                if tenant:
                    tenant_id = tenant.id

            await event_bus.publish(
                "audit.log",
                {
                    "action": "auth.login",
                    "entity_type": "user",
                    "entity_id": str(user_id) if user_id else None,
                    "tenant_id": str(tenant_id) if tenant_id else "0",  # 0 or system tenant if unknown
                    "user_id": str(user_id) if user_id else None,
                    "details": {
                        "username": username,
                        "success": success,
                        "reason": reason,
                    },
                    "ip_address": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                },
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.error("failed_to_publish_audit_log", error=str(e))
            # SECURITY FIX: Make audit log failure mode configurable
            # fail_open: Log error but allow operation (default, availability focused)
            # fail_closed: Raise exception and block operation (security focused, compliance)
            # Note: fail_closed requires ServiceUnavailableException to be defined
            if getattr(settings, "AUDIT_LOG_FAILURE_MODE", "fail_open") == "fail_closed":
                # This would require ServiceUnavailableException to be defined
                # For now, we log the error and continue (fail_open behavior)
                logger.warning(
                    "audit_log_fail_closed_not_implemented",
                    message="AUDIT_LOG_FAILURE_MODE='fail_closed' requested but exception type not available",
                )

    async def login(
        self,
        username: str,
        password: str,
        response: Response,
        request: Request,
        db: AsyncSession,
        redis: Redis,
    ) -> dict[str, str]:
        """Authenticate user and generate tokens.

        Parameters
        ----------
        username : str
            Username or email
        password : str
            User password
        response : Response
            FastAPI response object for setting cookies
        request : Request
            FastAPI request object for audit logging
        db : AsyncSession
            Database session
        redis : Redis
            Redis client for account lockout

        Returns
        -------
        dict[str, str]
            Access token and token type

        Raises
        ------
        UnauthorizedException
            If credentials are invalid or account is locked
        """
        # SECURITY FIX: Normalize username for consistent lockout tracking
        # Prevents bypass via case variations (e.g., User@example.com vs user@example.com)
        normalized_username = username.lower().strip()
        login_attempts_key = f"login_attempts:{normalized_username}"

        # SECURITY FIX: Check lockout BEFORE authentication to prevent CPU exhaustion (DoS)
        # Atomic Lua script: check lockout + increment in single roundtrip
        # Returns: -1 if locked out, otherwise the new attempt count
        # This records the attempt tentatively. If login succeeds, we clear it.
        result = await redis.eval(  # type: ignore[misc]
            self._LOGIN_CHECK_LUA,
            1,
            login_attempts_key,
            str(settings.AUTH_MAX_LOGIN_ATTEMPTS),
            str(settings.AUTH_LOCKOUT_DURATION_SECONDS),
        )

        # BUG FIX: Coerce result to int to handle bytes when Redis client is not initialized
        # with decode_responses=True. Without this, b'-1' != -1, disabling lockout entirely.
        try:
            result = int(result)
        except (TypeError, ValueError):
            result = 0  # Default: allow login attempt if coercion fails

        if result == -1:
            await self._publish_login_audit(request, username, False, reason="account_locked")
            raise UnauthorizedException("Wrong username, email or password.")

        # Authenticate (this takes time due to bcrypt)
        user = await self.authenticate_user(username_or_email=username, password=password, db=db)

        if not user:
            await self._publish_login_audit(request, username, False, reason="invalid_credentials")
            raise UnauthorizedException("Wrong username, email or password.")

        if not user.get("is_active", False):
            await self._publish_login_audit(request, username, False, reason="account_inactive")
            raise UnauthorizedException("User account is no longer active.")

        # Clear failed attempts on success (single roundtrip)
        await redis.delete(login_attempts_key)

        # Check if MFA is enabled for this user
        mfa_service = MFAService()
        mfa_enabled = await mfa_service.is_mfa_enabled(user["id"], db)

        if mfa_enabled:
            # Generate MFA pending token
            mfa_token_manager = MFAPendingTokenManager(redis)
            mfa_token = await mfa_token_manager.create_token(
                user_id=user["id"],
                username=user["username"],
            )

            # Return partial authentication response
            await self._publish_login_audit(
                request,
                username,
                True,
                tenant_id=user["tenant_id"],
                user_id=user["id"],
            )

            return {
                "mfa_required": "true",
                "mfa_token": mfa_token,
                "expires_in": str(MFA_PENDING_EXPIRY_SECONDS),
                "message": "MFA verification required",
            }

        # Include tier info in JWT claims for Zero-DB rate limiting
        # tier_name allows middleware to apply correct limits without DB lookup
        # SECURITY FIX: Include token_version for session invalidation on password change
        # SECURITY FIX: Include tenant_id for tenant context validation
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        token_data = {
            "sub": user["username"],
            "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
            "token_version": user.get("token_version", 0),
        }

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        # Generate both tokens in parallel using asyncio.gather for better performance
        access_token, refresh_token = await asyncio.gather(
            create_access_token(data=token_data, expires_delta=access_token_expires),
            create_refresh_token(data=token_data),
        )
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

        # Cookie security settings based on environment
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        # Cookie security settings
        # SECURITY FIX: Always use secure=True for refresh tokens
        # Prevents transmission over HTTP in any environment
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,  # Always secure - refresh tokens are high-value
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        # Audit log success
        await self._publish_login_audit(request, username, True, tenant_id=user["tenant_id"], user_id=user["id"])

        # Register session for tracking using the singleton session_manager
        try:
            # Extract jti from access token for session ID
            import json

            import jwt

            from src.app.core.security.session_manager import session_manager as global_session_manager

            decoded_token = jwt.decode(access_token, options={"verify_signature": False})
            session_id = decoded_token.get("jti", access_token[:32])
            client_ip = request.client.host if request.client else "Unknown"

            # Store session info as structured JSON instead of fragile delimited string
            device_info_data = {
                "user_agent": request.headers.get("user-agent", "Unknown"),
                "ip": client_ip,
            }
            device_info_str = json.dumps(device_info_data)

            await global_session_manager.register_session(
                user_id=user["id"],
                session_id=session_id,
                device_info=device_info_str,
            )
        except Exception as e:
            # Session registration failure should not block login
            logger.warning("session_registration_failed", error=str(e))

        return {"access_token": access_token, "token_type": "bearer"}

    async def refresh_access_token(
        self, refresh_token: str | None, response: Response, db: AsyncSession
    ) -> dict[str, str]:
        """Refresh access token using refresh token.

        Implements rotation: invalidates old refresh token and issues a new one.
        Includes absolute session lifetime check to prevent indefinite refresh.
        """
        if not refresh_token:
            raise UnauthorizedException("Refresh token missing.")

        user_data = await verify_token(refresh_token, TokenType.REFRESH, db)
        if not user_data:
            raise UnauthorizedException("Invalid refresh token.")

        # SECURITY FIX: Check absolute session expiration to enforce maximum session lifetime
        # Use absolute_exp from already-verified token (avoid double JWT decode)
        absolute_exp = user_data.absolute_exp
        if absolute_exp:
            from datetime import UTC
            from datetime import datetime as dt

            if dt.fromtimestamp(absolute_exp, tz=UTC) < dt.now(UTC):
                raise UnauthorizedException(
                    "Session expired. Please log in again.",
                )

        # Blacklist the used refresh token (Rotation)
        await blacklist_token(token=refresh_token, db=db)

        # Fetch user with tier for Zero-DB rate limiting
        # Use UserService to benefit from auth info caching
        user_service = get_user_service(db)
        if "@" in user_data.username_or_email:
            # Fallback for email-based subjects (rare but safe)
            db_user = await user_service.get_user_auth_info(email=user_data.username_or_email)
        else:
            # Optimized cached path for username-based subjects (standard)
            db_user = await user_service.get_user_auth_info(username=user_data.username_or_email)

        # A1 SECURITY FIX: Block token refresh for deleted, non-existent, or inactive users
        if not db_user or db_user.get("is_deleted", False) or not db_user.get("is_active", False):
            raise UnauthorizedException("User account is no longer active.")

        # Include tier info in JWT claims for Zero-DB rate limiting
        tier = db_user.get("tier") if db_user else None
        tier_name = tier.get("name") if tier else None
        # SECURITY FIX: Include token_version for session invalidation
        # SECURITY FIX: Include tenant_id for tenant isolation during token refresh
        token_data = {
            "sub": user_data.username_or_email,
            "tenant_id": str(db_user.get("tenant_id")) if db_user and db_user.get("tenant_id") else None,
            "tier_id": db_user.get("tier_id") if db_user else None,
            "tier_name": tier_name,
            "token_version": db_user.get("token_version", 0) if db_user else 0,
        }

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

        # Create new tokens
        new_access_token, new_refresh_token = await asyncio.gather(
            create_access_token(data=token_data, expires_delta=access_token_expires),
            create_refresh_token(data=token_data),
        )

        # Cookie security settings based on environment
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        # Cookie security settings
        # SECURITY FIX: Always use secure=True for refresh tokens
        # Prevents transmission over HTTP in any environment
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,  # Always secure - refresh tokens are high-value
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        return {"access_token": new_access_token, "token_type": "bearer"}

    async def logout(
        self,
        access_token: str,
        refresh_token: str | None,
        response: Response,
        request: Request,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Logout user by blacklisting tokens.

        Parameters
        ----------
        access_token : str
            Access token to blacklist
        refresh_token : Optional[str]
            Refresh token from cookie
        response : Response
            FastAPI response object for deleting cookies
        request : Request
            FastAPI request object for audit logging
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If refresh token is missing or invalid
        """
        try:
            if not refresh_token:
                raise UnauthorizedException("Refresh token not found.")

            # Try to get user info from token before blacklisting to log who logged out
            user_data = None
            try:
                user_data = await verify_token(access_token, TokenType.ACCESS, db)
            except (UnauthorizedException, ExpiredSignatureError):
                pass  # Token expired or invalid, proceed with logout anyway

            await blacklist_tokens(access_token=access_token, refresh_token=refresh_token, db=db)
            response.delete_cookie(key="refresh_token")

            # Try to invalidate the specific session to decrement active sessions
            if user_data:
                try:
                    user_service = get_user_service(db)
                    if "@" in user_data.username_or_email:
                        db_user = await user_service.get_user_auth_info(email=user_data.username_or_email)
                    else:
                        db_user = await user_service.get_user_auth_info(username=user_data.username_or_email)

                    if db_user:
                        import jwt

                        from src.app.core.security.session_manager import session_manager as global_session_manager

                        decoded_token = jwt.decode(access_token, options={"verify_signature": False})
                        session_id = decoded_token.get("jti", access_token[:32])
                        await global_session_manager.invalidate_session(db_user["id"], session_id)
                except Exception as e:
                    logger.warning("failed_to_invalidate_session_on_logout", error=str(e))

            # Audit log logout
            try:
                username = user_data.username_or_email if user_data else "unknown"

                await event_bus.publish(
                    "audit.log",
                    {
                        "action": "auth.logout",
                        "entity_type": "user",
                        "entity_id": None,
                        "tenant_id": "0",
                        "user_id": None,
                        "details": {
                            "username": username,
                            "success": True,
                        },
                        "ip_address": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent"),
                    },
                    priority=EventPriority.NORMAL,
                )
            except Exception as e:
                logger.warning("failed_to_publish_logout_audit_log", error=str(e))

            return {"message": "Logged out successfully"}

        except JWTError as e:
            logger.error("logout_jwt_error", error=str(e))
            raise UnauthorizedException("Invalid token.")

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

        Parameters
        ----------
        user_id : int
            The user's ID
        current_password : str
            Current password for verification
        new_password : str
            New password to set
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If current password is incorrect
        """
        # SECURITY FIX: Only active users can change their password
        # Ensure we properly fallback to False when checking active status in cached payload manually
        user = await crud_users.get(db=db, id=user_id, is_deleted=False, is_active=True)
        if not user or (isinstance(user, dict) and not user.get("is_active", False)):
            raise UnauthorizedException("User not found.")

        # Verify current password
        if not await verify_password(current_password, user["hashed_password"]):
            raise UnauthorizedException("Current password is incorrect.")

        # SECURITY FIX: Enforce password complexity
        try:
            from src.app.modules.users.schemas import UserCreate
            UserCreate.validate_password_complexity(new_password)
        except ValueError as e:
            from src.app.core.exceptions.http_exceptions import BadRequestException
            raise BadRequestException(str(e))

        # Hash and update new password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=user_id, object={"hashed_password": new_hashed})

        # SECURITY FIX: Invalidate ALL user sessions
        await self._invalidate_all_user_sessions(user, db)

        # Commit the combined transaction of password change + token version increment
        await db.commit()

        logger.info(
            "Password changed successfully",
            extra={"user_id": user["id"]},
        )

        # Invalidate auth cache so new password works immediately
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        # SECURITY FIX: Add audit log for password change (H-13)
        try:
            from src.app.core.events.bus import event_bus
            from src.app.core.events.models import EventPriority

            await event_bus.publish(
                "audit.log",
                {
                    "action": "auth.password_changed",
                    "entity_type": "user",
                    "entity_id": str(user_id),
                    "tenant_id": str(user.get("tenant_id", "0")),
                    "user_id": user_id,
                    "details": {"message": "User password was changed"},
                },
                priority=EventPriority.HIGH,
            )
        except Exception as e:
            logger.error("failed_to_publish_password_change_audit_log", error=str(e))

        return {"message": "Password changed successfully. All other sessions have been logged out."}

    async def _invalidate_all_user_sessions(self, user: dict[str, Any], db: AsyncSession) -> None:
        """Invalidate all active sessions for a user.

        This implementation uses token_versioning for instant global invalidation.
        When token_version is incremented in the DB, all previously issued tokens
        (which contain the old version) will be rejected by verify_token().
        """
        # SECURITY FIX: Increment token_version to invalidate all existing tokens
        # This ensures any tokens issued before password change are invalid.
        # FIXED: Removed redundant creation/blacklisting of dummy tokens (Item 11b).
        # Token version check in verify_token() is the primary and sufficient mechanism.
        user_id = user.get("id")
        await db.execute(update(User).where(User.id == user_id).values(token_version=User.token_version + 1))
        # Note: We do NOT commit here to allow callers to manage their own transactions (e.g. change_password)

        logger.info("invalidated_all_user_sessions", user_id=user_id)

    async def request_password_reset(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Request password reset (sends email with reset token).

        Parameters
        ----------
        email : str
            User's email address
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message (always returns success for security)
        """
        # BUG FIX: Also require is_active=True to prevent inactive accounts from
        # resetting their password without going through account reactivation.
        user = await crud_users.get(db=db, email=email, is_deleted=False, is_active=True)

        # Always return success to prevent email enumeration
        if user:
            # Create password reset token (expires in 1 hour)
            reset_token = PasswordResetToken(user_id=user["id"])
            db.add(reset_token)
            await db.commit()
            await db.refresh(reset_token)

            logger.info("Password reset token created", user_id=user["id"], token_id=reset_token.id)

            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

            # BUG FIX: Previously published directly to "notification.send" with keys
            # `template_name` and `context`. The only subscriber requires `notification_id`,
            # `subject`, and `html_content` (pre-rendered). The mismatched payload caused the
            # event to be silently dropped → password reset emails were NEVER sent.
            # Fix: delegate to NotificationService.send_email() which handles template
            # rendering, creates a Notification DB record, and emits the correctly-shaped event.
            from uuid import UUID as _UUID

            from src.app.modules.notifications.service import NotificationService

            notification_service = NotificationService(db)
            await notification_service.send_email(
                tenant_id=_UUID(str(user["tenant_id"])),
                to_email=email,
                template_name="password_reset",
                context={
                    "username": user["username"],
                    "reset_link": reset_link,
                    "valid_minutes": 60,
                },
                user_id=user["id"],
            )
        else:
            # SECURITY FIX: Equalize timing for enumeration prevention
            # Simulate DB and event bus operations to match the "user exists" path timing
            await asyncio.sleep(0.15)  # Simulate DB write and event publish latency

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

        Parameters
        ----------
        token : str
            Password reset token
        new_password : str
            New password to set
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If token is invalid, expired, or account is deleted/inactive
        """
        # SECURITY FIX: Use SELECT FOR UPDATE to prevent concurrent token reuse.
        # Without this lock, two concurrent requests with the same token could both
        # pass the is_valid check before either marks the token as used.
        result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token == token).with_for_update())
        reset_token = result.scalar_one_or_none()

        if not reset_token:
            raise UnauthorizedException("Invalid password reset token.")

        if not reset_token.is_valid:
            if reset_token.used:
                raise UnauthorizedException("This password reset token has already been used.")
            if reset_token.is_expired:
                raise UnauthorizedException("This password reset token has expired.")
            raise UnauthorizedException("Invalid password reset token.")

        # SECURITY FIX: Get user WITHOUT is_deleted filter to check account status
        user = await crud_users.get(db=db, id=reset_token.user_id)
        if not user:
            raise UnauthorizedException("Invalid password reset token.")

        # SECURITY FIX: Use generic message for deleted/inactive accounts
        # Prevents account enumeration attacks - attackers cannot determine account status
        if user.get("is_deleted", False) or not user.get("is_active", False):
            raise UnauthorizedException("This account is not accessible. Please contact support for assistance.")

        # Mark token as used FIRST (before password change)
        # SECURITY FIX: Prevents token reuse if password change fails
        reset_token.used = True
        await db.flush()  # Ensure token is marked as used before proceeding

        # SECURITY FIX: Enforce password complexity
        try:
            from src.app.modules.users.schemas import UserCreate
            UserCreate.validate_password_complexity(new_password)
        except ValueError as e:
            from src.app.core.exceptions.http_exceptions import BadRequestException
            raise BadRequestException(str(e))

        # Update password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=reset_token.user_id, object={"hashed_password": new_hashed})

        # Invalidate ALL user sessions after password reset
        await self._invalidate_all_user_sessions(user, db)

        # Invalidate auth cache
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        await db.commit()

        logger.info("Password reset successful", user_id=reset_token.user_id, token_id=reset_token.id)

        return {"message": "Password reset successfully. Please log in with your new password."}

    async def send_verification_email(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Send email verification link.

        Parameters
        ----------
        email : str
            User's email address
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message
        """

        # BUG FIX: Also require is_active=True to prevent inactive accounts from
        # verifying emails (which could restore account access without going through reactivation).
        user = await crud_users.get(db=db, email=email, is_deleted=False, is_active=True)

        if user and not user.get("is_email_verified", False):
            # Create verification token (defaults handle generation)
            token_model = EmailVerificationToken(user_id=user["id"])
            db.add(token_model)
            await db.commit()
            await db.refresh(token_model)

            verification_token = token_model.token

            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={verification_token}"

            # BUG FIX: Use NotificationService for correctly shaped events
            from uuid import UUID as _UUID

            from src.app.modules.notifications.service import NotificationService

            notification_service = NotificationService(db)
            await notification_service.send_email(
                tenant_id=_UUID(str(user["tenant_id"])),
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
            await asyncio.sleep(0.15)

        return {"message": "If the email is registered and unverified, a verification link has been sent."}

    async def verify_email(
        self,
        token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Verify email using verification token.

        Parameters
        ----------
        token : str
            Email verification token
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If token is invalid or expired
        """
        # Find token in database
        stmt = select(EmailVerificationToken).where(EmailVerificationToken.token == token).with_for_update()
        result = await db.execute(stmt)
        verification_token = result.scalar_one_or_none()

        if not verification_token:
            raise UnauthorizedException("Invalid verification token.")

        if verification_token.is_expired:
            raise UnauthorizedException("Verification token has expired.")

        # Get user
        user = await crud_users.get(db=db, id=verification_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # BUG FIX: Reject verification for inactive accounts. Without this check,
        # an inactive user could verify their email and gain unintended access.
        if not user.get("is_active", True):
            raise UnauthorizedException("This account is inactive. Please contact support.")

        # Update user status
        await crud_users.update(db=db, id=verification_token.user_id, object={"is_email_verified": True})

        # Invalidate auth cache
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        # Delete token (it's single use)
        await db.delete(verification_token)
        await db.commit()

        return {"message": "Email verified successfully."}
