"""Auth service layer - encapsulates all authentication business logic."""

import asyncio
import random
from datetime import timedelta
from typing import Any, cast
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
from src.app.core.security.tokens import hash_token
from src.app.core.service_registry import get_service
from src.app.core.tenancy.context import get_tenant_context
from src.app.modules.auth.models import (
    EmailVerificationToken,
    PasswordResetToken,
    generate_reset_token,
    generate_verification_token,
)

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
    _DUMMY_BCRYPT_HASH = "$2b$12$DOUePotH1f1kDX4WZ21DzuRgFk7C8xFhasEURTDHCx1KnvQstFIvi"  # noqa: S105

    # Atomic Lua script for login attempt checking.
    # Combines lockout check + increment + expire into single Redis roundtrip.
    # Returns -1 if locked out, otherwise the new attempt count.
    # OPT-11: Always execute EXPIRE to ensure constant-time Redis operations regardless of user existence.
    _LOGIN_CHECK_LUA = """
local attempts = redis.call('GET', KEYS[1])
if attempts and tonumber(attempts) >= tonumber(ARGV[1]) then
    return -1
end
local new_attempts = redis.call('INCR', KEYS[1])
-- SECURITY FIX: Always set/refresh expiry to ensure constant timing and prevent enumeration
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
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
        user_service = get_service("user", db)
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

        return cast(dict[str, Any], db_user)

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
            if getattr(settings.auth, "AUDIT_LOG_FAILURE_MODE", "fail_open") == "fail_closed":
                from fastapi import HTTPException

                raise HTTPException(status_code=503, detail="Service temporarily unavailable") from e

    async def login(
        self,
        username: str,
        password: str,
        response: Response,
        request: Request,
        db: AsyncSession,
        redis: Redis,
    ) -> dict[str, Any]:
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
            result = -1  # Fail-closed: default to locked on Redis error

        if result == -1:
            # Fetch user to get tenant and user ID for audit log
            user_service = get_service("user", db)
            if "@" in normalized_username:
                db_user = await user_service.get_user_auth_info(email=normalized_username)
            else:
                db_user = await user_service.get_user_auth_info(username=normalized_username)

            tenant_id = db_user.get("tenant_id") if db_user else None
            user_id = db_user.get("id") if db_user else None

            await self._publish_login_audit(
                request, username, False, user_id=user_id, tenant_id=tenant_id, reason="account_locked"
            )
            raise UnauthorizedException("Wrong username, email or password.")

        # Authenticate (this takes time due to bcrypt)
        # SECURITY FIX: Use normalized_username (already computed for lockout key)
        # to ensure the credential lookup and lockout tracking operate on the same form.
        user = await self.authenticate_user(username_or_email=normalized_username, password=password, db=db)

        if not user:
            await self._publish_login_audit(request, username, False, reason="invalid_credentials")
            raise UnauthorizedException("Wrong username, email or password.")

        if not user.get("is_active", False):
            await self._publish_login_audit(request, username, False, reason="account_inactive")
            raise UnauthorizedException("User account is no longer active.")

        # Clear failed attempts on success (single roundtrip)
        await redis.delete(login_attempts_key)

        # Check if MFA is enabled for this user
        from src.app.modules.mfa.service import get_mfa_service

        mfa_service = get_mfa_service()
        mfa_enabled = await mfa_service.is_mfa_enabled(user["id"], db)

        if mfa_enabled:
            from src.app.core.utils.ip_utils import get_client_ip

            client_ip = get_client_ip(request)

            # Generate MFA pending token
            mfa_token_manager = MFAPendingTokenManager(redis)
            mfa_token = await mfa_token_manager.create_token(
                user_id=user["id"],
                username=user["username"],
                ip_address=client_ip,
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
                "mfa_required": True,
                "mfa_token": mfa_token,
                "expires_in": str(settings.MFA_PENDING_EXPIRY_SECONDS),
                "message": "MFA verification required",
            }

        return await self._generate_auth_tokens(user, response, request, username)

    async def _generate_auth_tokens(
        self,
        user: dict[str, Any],
        response: Response,
        request: Request,
        username: str,
    ) -> dict[str, str]:
        """Generate access and refresh tokens, set cookies, register session.

        Args:
            user: Authenticated user data dict.
            response: FastAPI response object for setting cookies.
            request: FastAPI request object for session tracking.
            username: Original username for audit logging.

        Returns:
            dict with access_token and token_type.
        """
        # Include tier info in JWT claims for Zero-DB rate limiting
        # tier_name allows middleware to apply correct limits without DB lookup
        # SECURITY FIX: Include token_version for session invalidation on password change
        # SECURITY FIX: Include tenant_id for tenant context validation
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        token_data: dict[str, Any] = {
            "sub": user["username"],
            "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
            "token_version": user.get("token_version", 0),
        }

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # Generate both tokens in parallel using asyncio.gather for better performance
        access_token, refresh_token = await asyncio.gather(
            create_access_token(data=token_data, expires_delta=access_token_expires),
            create_refresh_token(data=token_data),
        )
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

        # Cookie security settings based on environment
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        # SECURITY FIX: Always use secure=True for refresh tokens
        # Prevents transmission over HTTP in any environment
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        # Audit log success
        await self._publish_login_audit(request, username, True, tenant_id=user["tenant_id"], user_id=user["id"])

        # Register session for tracking using the singleton session_manager
        try:
            # Extract jti from access token for session ID
            import json

            from src.app.core.security.jwt_utils import decode_jwt_token, validate_jwt_length
            from src.app.core.security.session_manager import session_manager as global_session_manager

            # SECURITY: Validate token length and verify signature for session registration
            # Even if we just created the token, using verified decode prevents session fixation
            # and ensures we only track tokens we actually signed.
            if not validate_jwt_length(access_token):
                logger.warning("JWT decode blocked: token too long")
                decoded_token: dict[str, Any] = {}
            else:
                decoded_token = await decode_jwt_token(access_token) or {}
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
        self, refresh_token: str | None, response: Response, db: AsyncSession, request: Request | None = None
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

        # Fetch user with tier for Zero-DB rate limiting
        # Use UserService to benefit from auth info caching
        user_service = get_service("user", db)
        if "@" in user_data.username_or_email:
            # Fallback for email-based subjects (rare but safe)
            db_user = await user_service.get_user_auth_info(email=user_data.username_or_email)
        else:
            # Optimized cached path for username-based subjects (standard)
            db_user = await user_service.get_user_auth_info(username=user_data.username_or_email)

        # A1 SECURITY FIX: Block token refresh for deleted, non-existent, or inactive users
        if not db_user or db_user.get("is_deleted", False) or not db_user.get("is_active", False):
            raise UnauthorizedException("User account is no longer active.")

        # SECURITY FIX: Check token version to enforce password change/all-session invalidation
        # `user_data.token_version` is pulled from the refresh token by `verify_token`
        current_token_version = db_user.get("token_version", 0)
        if hasattr(user_data, "token_version") and user_data.token_version != current_token_version:
            raise UnauthorizedException("Session invalidated due to password change or security event.")

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

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

        # Create new tokens FIRST to ensure atomicity. If this fails, the user is not left locked out.
        new_access_token, new_refresh_token = await asyncio.gather(
            create_access_token(data=token_data, expires_delta=access_token_expires),
            create_refresh_token(data=token_data),
        )

        # Blacklist the used refresh token (Rotation) AFTER new tokens are successfully generated
        await blacklist_token(token=refresh_token, db=db)

        # SECURITY FIX: Invalidate old session in SessionManager to prevent session proliferation
        try:
            from src.app.core.security.session_manager import session_manager as global_session_manager

            if user_data.jti:
                await global_session_manager.invalidate_session(db_user["id"], user_data.jti)
        except Exception as e:
            logger.warning("failed_to_invalidate_old_session_on_refresh", error=str(e))

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
            secure=True,
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        try:
            import json

            from src.app.core.security.jwt_utils import extract_jti_unverified
            from src.app.core.security.session_manager import session_manager as global_session_manager

            session_id = extract_jti_unverified(new_access_token) or new_access_token[:32]

            client_ip = request.client.host if request and request.client else "Unknown"

            device_info_str = json.dumps(
                {"user_agent": request.headers.get("user-agent", "Unknown") if request else "Unknown", "ip": client_ip}
            )

            await global_session_manager.register_session(
                user_id=db_user["id"],
                session_id=session_id,
                device_info=device_info_str,
            )
        except Exception as e:
            logger.warning("session_registration_failed", error=str(e))

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
            db_user = None
            user_service = None
            if user_data:
                try:
                    user_service = get_service("user", db)
                    if "@" in user_data.username_or_email:
                        db_user = await user_service.get_user_auth_info(email=user_data.username_or_email)
                    else:
                        db_user = await user_service.get_user_auth_info(username=user_data.username_or_email)

                    if db_user:
                        from src.app.core.security.jwt_utils import extract_jti_unverified
                        from src.app.core.security.session_manager import session_manager as global_session_manager

                        session_id = extract_jti_unverified(access_token) or access_token[:32]
                        await global_session_manager.invalidate_session(db_user["id"], session_id)
                except Exception as e:
                    logger.warning("failed_to_invalidate_session_on_logout", error=str(e))

            # Audit log logout — reuse db_user from session invalidation (saves 1 DB query)
            try:
                username = user_data.username_or_email if user_data else "unknown"
                user_id_val = None
                tenant_id_val = "0"

                if db_user:
                    user_id_val = str(db_user["id"])
                    tenant_id_val = str(db_user.get("tenant_id", "0"))

                await event_bus.publish(
                    "audit.log",
                    {
                        "action": "auth.logout",
                        "entity_type": "user",
                        "entity_id": user_id_val,
                        "tenant_id": tenant_id_val,
                        "user_id": user_id_val,
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
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, id=user_id, is_deleted=False, is_active=True)
        if not user:
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
        await user_service.crud.update(db=db, id=user_id, object={"hashed_password": new_hashed}, commit=False)

        # SECURITY FIX: Invalidate ALL user sessions
        await self._invalidate_all_user_sessions(user, db)

        # Commit the combined transaction of password change + token version increment
        await db.commit()

        # Invalidate auth cache so new password works immediately, AFTER commit
        # to prevent race condition where concurrent reads see old DB value and re-cache it.
        user_service = get_service("user", db)
        await user_service._invalidate_user_cache(user)
        await self._delete_user_token_cache(user)

        logger.info(
            "Password changed successfully",
            extra={"user_id": user["id"]},
        )

        # SECURITY FIX: Add audit log for password change (H-13)
        try:
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
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.error("failed_to_publish_password_change_audit_log", error=str(e))

        return {"message": "Password changed successfully. All other sessions have been logged out."}

    async def _invalidate_all_user_sessions(self, user: dict[str, Any], db: AsyncSession) -> None:
        """Increment token_version in DB to invalidate all active sessions for a user.

        This implementation uses token_versioning for instant global invalidation.
        When token_version is incremented in the DB, all previously issued tokens
        (which contain the old version) will be rejected by verify_token().

        NOTE: Cache invalidation (via _delete_user_token_cache) should be called
        separately AFTER the DB transaction is committed to prevent race conditions.
        """
        user_id = user.get("id")

        user_service = get_service("user", db)
        User = user_service.model
        # Update DB - increment token_version
        await db.execute(update(User).where(User.id == user_id).values(token_version=User.token_version + 1))
        # Note: We do NOT commit here to allow callers to manage their own transactions (e.g. change_password)

        logger.info("invalidated_all_user_sessions_in_db", user_id=user_id)

    async def _delete_user_token_cache(self, user: dict[str, Any]) -> None:
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
            from src.app.core.utils.cache import client as redis_client

            if redis_client:
                username = user.get("username")
                email = user.get("email")
                tenant_id = user.get("tenant_id")
                keys = []
                if username:
                    keys.append(
                        f"tenant:{tenant_id}:user:{username}:token_version"
                        if tenant_id
                        else f"user:{username}:token_version"
                    )
                if email:
                    keys.append(
                        f"tenant:{tenant_id}:user:{email}:token_version" if tenant_id else f"user:{email}:token_version"
                    )
                if keys:
                    await redis_client.delete(*keys)
                    logger.debug("deleted_user_token_cache", user_id=user_id, keys=keys)
        except Exception as e:
            logger.warning("Failed to delete token_version cache", user_id=user_id, error=str(e))

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
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, email=email, is_deleted=False, is_active=True)

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

            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"
            # BUG FIX: Previously published directly to "notification.send" with keys
            # `template_name` and `context`. The only subscriber requires `notification_id`,
            # `subject`, and `html_content` (pre-rendered). The mismatched payload caused the
            # event to be silently dropped → password reset emails were NEVER sent.
            # Fix: delegate to "notification.request" event which NotificationService listens to.
            await event_bus.publish(
                "notification.request",
                {
                    "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
                    "to_email": email,
                    "template_name": "password_reset",
                    "context": {
                        "reset_link": reset_link,
                        "username": user["username"],
                    },
                    "user_id": user["id"],
                },
                priority=EventPriority.NORMAL,
            )
        else:
            # SECURITY FIX: Equalize timing for enumeration prevention
            # Simulate DB and event bus operations to match the "user exists" path timing
            # Added jitter to prevent precise timing analysis
            await asyncio.sleep(0.15 + random.uniform(-0.05, 0.05))

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
        hashed_token = hash_token(token)
        stmt = select(PasswordResetToken).where(PasswordResetToken.token == hashed_token).with_for_update()
        result = await db.execute(stmt)
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
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, id=reset_token.user_id)
        if not user:
            raise UnauthorizedException("Invalid password reset token.")

        # SECURITY FIX: Use generic message for deleted/inactive accounts
        # Prevents account enumeration attacks - attackers cannot determine account status
        is_deleted = user.get("is_deleted", False) if isinstance(user, dict) else getattr(user, "is_deleted", False)
        is_active = user.get("is_active", False) if isinstance(user, dict) else getattr(user, "is_active", False)
        if is_deleted or not is_active:
            await asyncio.sleep(0.15 + random.uniform(-0.05, 0.05))
            raise UnauthorizedException("This account is not accessible. Please contact support for assistance.")

        # SECURITY FIX: Enforce password complexity BEFORE marking token as used
        try:
            from src.app.modules.users.schemas import UserCreate

            UserCreate.validate_password_complexity(new_password)
        except ValueError as e:
            from src.app.core.exceptions.http_exceptions import BadRequestException

            raise BadRequestException(str(e))

        # Mark token as used AFTER successful validation
        # SECURITY FIX: Prevents token reuse if password change fails later, but avoids burning on bad input
        reset_token.used = True
        await db.flush()  # Ensure token is marked as used before proceeding

        # Update password
        new_hashed = await get_password_hash(new_password)
        await user_service.crud.update(
            db=db, id=reset_token.user_id, object={"hashed_password": new_hashed}, commit=False
        )

        # Invalidate ALL user sessions after password reset
        await self._invalidate_all_user_sessions(user, db)

        await db.commit()

        # Invalidate auth cache AFTER commit
        user_service = get_service("user", db)
        await user_service._invalidate_user_cache(user)
        await self._delete_user_token_cache(user)

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
        user_service = get_service("user", db)
        user = await user_service.crud.get(db=db, email=email, is_deleted=False, is_active=True)

        is_email_verified = (
            user.get("is_email_verified", False)
            if isinstance(user, dict)
            else getattr(user, "is_email_verified", False)
        )
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
            from uuid import UUID as _UUID

            from src.app.modules.notifications.service import NotificationService

            notification_service = NotificationService(db)
            await notification_service.send_email(
                tenant_id=_UUID(str(user["tenant_id"])) if user.get("tenant_id") else None,
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

        # BUG FIX: Reject verification for inactive accounts. Without this check,
        # an inactive user could verify their email and gain unintended access.
        # SECURITY: Default to False (deny-by-default) if field is absent.
        is_active = user.get("is_active", False) if isinstance(user, dict) else getattr(user, "is_active", False)
        if not is_active:
            raise UnauthorizedException("This account is inactive. Please contact support.")

        # Update user status
        await user_service.crud.update(
            db=db, id=verification_token.user_id, object={"is_email_verified": True}, commit=False
        )

        # Invalidate auth cache
        user_service = get_service("user", db)
        await user_service._invalidate_user_cache(user)

        # Delete token (it's single use)
        await db.delete(verification_token)
        await db.commit()

        return {"message": "Email verified successfully."}
