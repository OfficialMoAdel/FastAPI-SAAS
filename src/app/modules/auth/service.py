"""Auth service layer - encapsulates all authentication business logic."""

from typing import Any, cast
from uuid import UUID

from fastapi import Request, Response
from jwt import ExpiredSignatureError
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.events import EventPriority, event_bus
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.core.security import (
    TokenType,
    blacklist_token,
    blacklist_tokens,
    get_mfa_pending_token_manager,
    get_password_hash,
    needs_rehash,
    verify_password,
    verify_token,
)
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.audit_logs import audit
from src.app.modules.auth.services.email_verification import EmailVerificationService
from src.app.modules.auth.services.lockout import LoginLockoutPolicy
from src.app.modules.auth.services.passwords import PasswordLifecycleService
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.modules.users.service import UserService

logger = get_logger(__name__)
__all__ = ["AuthService", "AuthGateway", "get_auth_service", "get_auth"]
# Re-exported for test backward compatibility
# (TODO: REMOVE once tests/unit/modules/auth no longer patch these module attributes)
event_bus = event_bus
get_current_tenant = get_current_tenant


def get_auth_service() -> "AuthService":
    """Factory function to get AuthService instance.

    Returns
    -------
    AuthService
        New AuthService instance.
    """
    return AuthService()




class AuthService:
    """Service class for authentication-related business logic."""

    # Precomputed dummy bcrypt hash for timing attack prevention.
    # Used when user doesn't exist to ensure constant-time authentication.
    _DUMMY_BCRYPT_HASH = "$2b$12$DOUePotH1f1kDX4WZ21DzuRgFk7C8xFhasEURTDHCx1KnvQstFIvi"  # noqa: S105

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
        user_service = UserService(db)
        if "@" in username_or_email:
            db_user = await user_service.get_user_auth_info(email=username_or_email)
        else:
            db_user = await user_service.get_user_auth_info(username=username_or_email)

        # SECURITY: Use dummy hash if user not found to prevent timing attacks.
        # This ensures bcrypt verification always takes ~100ms, preventing
        # attackers from enumerating valid usernames via response time analysis.
        hash_to_verify = db_user["hashed_password"] if db_user else self._DUMMY_BCRYPT_HASH
        # SSO-only accounts store a non-bcrypt sentinel ("!SSO_ONLY_ACCOUNT_NO_PASSWORD")
        # in place of a real password. Passing it to verify_password raises UnknownHashError
        # (500). Treat the sentinel as an invalid password so the login fails cleanly with 401.
        # Empty/missing hashes (e.g. partially migrated rows) are handled the same way.
        if not hash_to_verify or str(hash_to_verify).startswith("!SSO_ONLY"):
            # Still perform dummy bcrypt to prevent timing-based account enumeration
            await verify_password(password, self._DUMMY_BCRYPT_HASH)
            return None
        password_valid = await verify_password(password, hash_to_verify)

        # Only return user data if both user exists AND password is valid
        if not db_user or not password_valid:
            return None

        # A7 FIX: transparently upgrade legacy-cost hashes on successful login.
        # If BCRYPT_ROUNDS was raised since the hash was created, re-hash now
        # so the stored cost converges to the configured value over time.
        if needs_rehash(hash_to_verify):
            try:
                await user_service.update_password_hash(db_user["id"], await get_password_hash(password))
            except Exception as e:  # noqa: BLE001 - rehash is best-effort; never block login
                logger.warning("rehash_on_login_failed", user_id=db_user.get("id"), error=str(e))

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
            await audit(
                action="auth.login",
                entity_type="user",
                entity_id=user_id,
                tenant_id=tenant_id,
                user_id=user_id,
                details={
                    "username": username,
                    "success": success,
                    "reason": reason,
                },
                ip_address=get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.error("failed_to_publish_audit_log", error=str(e))
            # SECURITY FIX: Make audit log failure mode configurable
            # fail_open: Log error but allow operation (default, availability focused)
            # fail_closed: Raise exception and block operation (security focused, compliance)
            # Note: fail_closed requires ServiceUnavailableException to be defined
            if getattr(settings.auth, "AUDIT_LOG_FAILURE_MODE", "fail_open") == "fail_closed":
                from src.app.modules._base.exceptions import ServiceUnavailableError

                raise ServiceUnavailableError("Service temporarily unavailable") from e

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

        # SECURITY FIX: Check lockout BEFORE authentication to prevent CPU exhaustion (DoS)
        lockout = LoginLockoutPolicy()
        if await lockout.is_locked(redis, normalized_username):
            # Fetch user to get tenant and user ID for audit log
            user_service = UserService(db)
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
            await lockout.record_failed_attempt(redis, normalized_username)
            await self._publish_login_audit(request, username, False, reason="invalid_credentials")
            raise UnauthorizedException("Wrong username, email or password.")
        if not user.get("is_active", False):
            await self._publish_login_audit(request, username, False, reason="account_inactive")
            raise UnauthorizedException("User account is no longer active.")

        # Clear failed attempts on success (single roundtrip)
        await lockout.clear(redis, normalized_username)

        # Check if MFA is enabled for this user
        from src.app.modules.auth.services.mfa import get_mfa_service

        mfa_service = get_mfa_service()
        mfa_enabled = await mfa_service.is_mfa_enabled(user["id"], db)

        if mfa_enabled:
            from src.app.core.utils.ip_utils import get_client_ip

            client_ip = get_client_ip(request)

            # Generate MFA pending token
            from src.app.core.security.tokens import TokenGenerator

            mfa_token_store = get_mfa_pending_token_manager(redis)
            mfa_token = TokenGenerator.generate_magic_link_token()
            await mfa_token_store.create(
                token=mfa_token,
                data={
                    "user_id": user["id"],
                    "username": user["username"],
                    "ip_address": client_ip,
                    "scope": "mfa_pending",
                },
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
                "expires_in": int(settings.auth.MFA_PENDING_EXPIRY_SECONDS),
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
        issued = await TokenIssuer.issue_session(
            user,
            channel="password",
            request=request,
            response=response,
            subject=user["username"],
        )

        # Audit log success
        await self._publish_login_audit(request, username, True, tenant_id=user["tenant_id"], user_id=user["id"])

        return {"access_token": issued.access_token, "token_type": "bearer"}

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
        user_service = UserService(db)
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
        # SECURITY FIX: Include token_version for session invalidation
        # SECURITY FIX: Include tenant_id for tenant isolation during token refresh
        token_data = TokenIssuer.build_token_data(subject=user_data.username_or_email, user=db_user or {})

        # A2/A3: recover the OLD refresh payload so rotation can carry forward
        # absolute_exp, original iat and the stable sid. The token string has
        # already been signature-verified by verify_token() above; this decode
        # is a local operation to surface claims TokenData does not expose.
        from src.app.core.security.jwt_utils import decode_jwt_token

        old_refresh_payload = await decode_jwt_token(refresh_token) or {}

        # Create new tokens FIRST to ensure atomicity. If this fails, the user is not left locked out.
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        lock_key = None
        if redis_client and user_data.jti:
            lock_key = f"refresh_lock:{user_data.jti}"
            lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=30)
            if not lock_acquired:
                raise UnauthorizedException("Token refresh already in progress.")
        try:
            # A2/A3: rotate with carried session claims instead of minting fresh ones
            new_access_token, new_refresh_token = await TokenIssuer.rotate_token_pair(token_data, old_refresh_payload)

            # Blacklist the used refresh token (Rotation) AFTER new tokens are successfully generated
            # We don't pass the payload here so that blacklist_token decodes it fully to get the 'exp' claim
            await blacklist_token(token=refresh_token, db=db)
        finally:
            if redis_client and lock_key:
                try:
                    await redis_client.delete(lock_key)
                except Exception as e:
                    # Bounded by ex=30, but log for incident diagnosis
                    logger.debug("Failed to delete refresh lock key", lock_key=lock_key, error=str(e))
        # SECURITY FIX: Invalidate old session in SessionManager to prevent session proliferation
        # A3: keyed by the stable sid claim — the refresh token's jti never
        # matched the registered access-token jti, so the old call was a no-op.
        try:
            from src.app.core.security.session_manager import session_manager as global_session_manager

            if user_data.sid:
                await global_session_manager.invalidate_session(db_user["id"], user_data.sid)
        except Exception as e:
            logger.warning("failed_to_invalidate_old_session_on_refresh", error=str(e))

        # Cookie security settings based on environment
        from src.app.core.security.login_helpers import register_login_session, set_refresh_cookie

        set_refresh_cookie(response, new_refresh_token)

        await register_login_session(
            user_id=db_user["id"], access_token=new_access_token, request=request, verify=False
        )

        return {"access_token": new_access_token, "token_type": "bearer"}

    async def logout(
        self,
        access_token: str | None,
        refresh_token: str | None,
        response: Response,
        request: Request,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Logout user by blacklisting tokens.

        Parameters
        ----------
        access_token : str | None
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
            If neither access nor refresh token is provided, or a token is invalid
        """
        try:
            # A8 FIX: previously a missing refresh cookie raised 401, so
            # access-token-only clients (no cookies) could never log out.
            # Require at least one token; blacklist whatever is provided —
            # blacklist_tokens() already skips None entries.
            if not access_token and not refresh_token:
                raise UnauthorizedException("No token provided.")

            # Try to get user info from token before blacklisting to log who logged out
            user_data = None
            if access_token:
                try:
                    user_data = await verify_token(access_token, TokenType.ACCESS, db)
                except (UnauthorizedException, ExpiredSignatureError):
                    pass  # Token expired or invalid, proceed with logout anyway

            await blacklist_tokens(access_token=access_token, refresh_token=refresh_token, db=db)
            from src.app.core.security.login_helpers import clear_refresh_cookies

            clear_refresh_cookies(response)

            # Try to invalidate the specific session to decrement active sessions
            db_user = None
            user_service = None
            if user_data:
                try:
                    user_service = UserService(db)
                    if "@" in user_data.username_or_email:
                        db_user = await user_service.get_user_auth_info(email=user_data.username_or_email)
                    else:
                        db_user = await user_service.get_user_auth_info(username=user_data.username_or_email)

                    if db_user and access_token:
                        from src.app.core.security.jwt_utils import (
                            extract_claim_unverified,
                            extract_jti_unverified,
                        )
                        from src.app.core.security.session_manager import session_manager as global_session_manager

                        # A3: sessions are registered under the stable sid
                        # claim, not the per-token jti — invalidating by jti
                        # never matched the registry and was a silent no-op.
                        session_id = (
                            extract_claim_unverified(access_token, "sid")
                            or extract_jti_unverified(access_token)
                            or access_token[:32]
                        )
                        await global_session_manager.invalidate_session(db_user["id"], session_id)
                except Exception as e:
                    logger.warning("failed_to_invalidate_session_on_logout", error=str(e))

            # Audit log logout — reuse db_user from session invalidation (saves 1 DB query)
            try:
                username = user_data.username_or_email if user_data else "unknown"
                user_id_val = db_user["id"] if db_user else None
                tenant_id_val = db_user.get("tenant_id") if db_user else None

                await audit(
                    action="auth.logout",
                    entity_type="user",
                    entity_id=user_id_val,
                    tenant_id=tenant_id_val,
                    user_id=user_id_val,
                    details={
                        "username": username,
                        "success": True,
                    },
                    ip_address=get_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    priority=EventPriority.NORMAL,
                )
            except Exception as e:
                logger.warning("failed_to_publish_logout_audit_log", error=str(e))

            return {"message": "Logged out successfully"}

        except JWTError as e:
            logger.error("logout_jwt_error", error=str(e))
            raise UnauthorizedException("Invalid token.")

    # ------------------------------------------------------------------
    # Password lifecycle — delegated to PasswordLifecycleService
    # ------------------------------------------------------------------

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Change user password (delegates to PasswordLifecycleService)."""
        return await PasswordLifecycleService().change_password(user_id, current_password, new_password, db)

    async def _invalidate_all_user_sessions(self, user: dict[str, Any], db: AsyncSession) -> None:
        """Increment token_version to invalidate all sessions (see PasswordLifecycleService)."""
        await PasswordLifecycleService().invalidate_all_user_sessions(user, db)

    async def _delete_user_token_cache(self, user: dict[str, Any]) -> None:
        """Delete token_version cache keys for a user (see PasswordLifecycleService)."""
        await PasswordLifecycleService().delete_user_token_cache(user)

    async def request_password_reset(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Request password reset (delegates to PasswordLifecycleService)."""
        return await PasswordLifecycleService().request_password_reset(email, db)

    async def reset_password(
        self,
        token: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Reset password using reset token (delegates to PasswordLifecycleService)."""
        return await PasswordLifecycleService().reset_password(token, new_password, db)

    # ------------------------------------------------------------------
    # Email verification — delegated to EmailVerificationService
    # ------------------------------------------------------------------

    async def send_verification_email(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Send email verification link (delegates to EmailVerificationService)."""
        return await EmailVerificationService().send_verification_email(email, db)

    async def verify_email(
        self,
        token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Verify email using verification token (delegates to EmailVerificationService)."""
        return await EmailVerificationService().verify_email(token, db)

get_auth = get_auth_service
AuthGateway = AuthService
