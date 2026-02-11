"""Auth service layer - encapsulates all authentication business logic."""

import asyncio
from datetime import timedelta
from typing import Any

from fastapi import Request, Response
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from ...core.config import EnvironmentOption, settings
from ...core.events import EventPriority, event_bus
from ...core.exceptions.http_exceptions import UnauthorizedException
from ...core.security import (
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
from ...core.tenancy.context import get_tenant_context
from ...modules.mfa.service import MFAService
from ...modules.users.crud import crud_users
from ...modules.users.service import get_user_service
from .models import EmailVerificationToken, PasswordResetToken

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
        tenant_id: int | None = None,
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
        login_attempts_key = f"login_attempts:{username}"

        # Atomic Lua script: check lockout + increment in single roundtrip
        # Returns: -1 if locked out, otherwise the new attempt count
        result = await redis.eval(  # type: ignore[misc]
            self._LOGIN_CHECK_LUA,
            1,
            login_attempts_key,
            str(settings.AUTH_MAX_LOGIN_ATTEMPTS),
            str(settings.AUTH_LOCKOUT_DURATION_SECONDS),
        )

        if result == -1:
            lockout_minutes = settings.AUTH_LOCKOUT_DURATION_SECONDS // 60
            await self._publish_login_audit(request, username, False, reason="account_locked")
            raise UnauthorizedException(
                f"Account locked due to too many failed attempts. Please try again in {lockout_minutes} minutes."
            )

        user = await self.authenticate_user(username_or_email=username, password=password, db=db)
        if not user:
            await self._publish_login_audit(request, username, False, reason="invalid_credentials")
            raise UnauthorizedException("Wrong username, email or password.")

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
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        token_data = {
            "sub": user["username"],
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
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
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=is_production,
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        # Audit log success
        await self._publish_login_audit(request, username, True, tenant_id=user["tenant_id"], user_id=user["id"])

        return {"access_token": access_token, "token_type": "bearer"}

    async def refresh_access_token(
        self, refresh_token: str | None, response: Response, db: AsyncSession
    ) -> dict[str, str]:
        """Refresh access token using refresh token.

        Implements rotation: invalidates old refresh token and issues a new one.
        """
        if not refresh_token:
            raise UnauthorizedException("Refresh token missing.")

        user_data = await verify_token(refresh_token, TokenType.REFRESH, db)
        if not user_data:
            raise UnauthorizedException("Invalid refresh token.")

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

        # A1 SECURITY FIX: Block token refresh for deleted or non-existent users
        if not db_user or db_user.get("is_deleted", False):
            raise UnauthorizedException("User account is no longer active.")

        # Include tier info in JWT claims for Zero-DB rate limiting
        tier = db_user.get("tier") if db_user else None
        tier_name = tier.get("name") if tier else None
        token_data = {
            "sub": user_data.username_or_email,
            "tier_id": db_user.get("tier_id") if db_user else None,
            "tier_name": tier_name,
        }

        # Create new tokens
        new_access_token = await create_access_token(data=token_data)
        new_refresh_token = await create_refresh_token(data=token_data)

        # Cookie security settings based on environment
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=is_production,
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
            except Exception:
                pass  # Token might be expired, proceed with logout anyway

            await blacklist_tokens(access_token=access_token, refresh_token=refresh_token, db=db)
            response.delete_cookie(key="refresh_token")

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
        user = await crud_users.get(db=db, id=user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Verify current password
        if not await verify_password(current_password, user["hashed_password"]):
            raise UnauthorizedException("Current password is incorrect.")

        # Hash and update new password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=user_id, object={"hashed_password": new_hashed})

        # Invalidate auth cache so new password works immediately
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        return {"message": "Password changed successfully"}

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
        user = await crud_users.get(db=db, email=email, is_deleted=False)

        # Always return success to prevent email enumeration
        if user:
            # Create password reset token (expires in 1 hour)
            reset_token = PasswordResetToken(user_id=user["id"])
            db.add(reset_token)
            await db.commit()
            await db.refresh(reset_token)

            logger.info("Password reset token created", user_id=user["id"], token_id=reset_token.id)

            # Send email via event bus
            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

            await event_bus.publish(
                "notification.send",
                {
                    "to_email": email,
                    "template_name": "password_reset",
                    "context": {
                        "username": user["username"],
                        "reset_link": reset_link,
                        "valid_minutes": 60,
                    },
                    "tenant_id": str(user["tenant_id"]),
                    "user_id": str(user["id"]),
                },
                priority=EventPriority.NORMAL,
            )

        return {"message": "If an account exists with that email, a reset link has been sent."}

    async def reset_password(
        self,
        token: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Reset password using reset token.

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
            If token is invalid or expired
        """
        # Find token in database
        result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token == token))
        reset_token = result.scalar_one_or_none()

        if not reset_token:
            raise UnauthorizedException("Invalid password reset token.")

        if not reset_token.is_valid:
            if reset_token.used:
                raise UnauthorizedException("This password reset token has already been used.")
            if reset_token.is_expired:
                raise UnauthorizedException("This password reset token has expired.")
            raise UnauthorizedException("Invalid password reset token.")

        # Get user
        user = await crud_users.get(db=db, id=reset_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Update password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=reset_token.user_id, object={"hashed_password": new_hashed})

        # Invalidate auth cache
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        # Mark token as used
        reset_token.used = True
        await db.commit()

        logger.info("Password reset successful", user_id=reset_token.user_id, token_id=reset_token.id)

        return {"message": "Password reset successfully."}

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

        user = await crud_users.get(db=db, email=email, is_deleted=False)

        if user and not user.get("is_email_verified", False):
            # Create verification token (defaults handle generation)
            token_model = EmailVerificationToken(user_id=user["id"])
            db.add(token_model)
            await db.commit()
            await db.refresh(token_model)

            verification_token = token_model.token

            # Send email via event bus
            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={verification_token}"

            await event_bus.publish(
                "notification.send",
                {
                    "to_email": email,
                    "template_name": "email_verification",
                    "context": {
                        "username": user["username"],
                        "verification_link": verification_link,
                    },
                    "tenant_id": str(user["tenant_id"]),
                    "user_id": str(user["id"]),
                },
                priority=EventPriority.NORMAL,
            )

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
        result = await db.execute(select(EmailVerificationToken).where(EmailVerificationToken.token == token))
        verification_token = result.scalar_one_or_none()

        if not verification_token:
            raise UnauthorizedException("Invalid verification token.")

        if verification_token.is_expired:
            raise UnauthorizedException("Verification token has expired.")

        # Get user
        user = await crud_users.get(db=db, id=verification_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Update user status
        await crud_users.update(db=db, id=verification_token.user_id, object={"is_email_verified": True})

        # Invalidate auth cache
        user_service = get_user_service(db)
        await user_service._invalidate_user_cache(user)

        # Delete token (it's single use)
        await db.delete(verification_token)
        await db.commit()

        return {"message": "Email verified successfully."}
