"""Auth service layer - encapsulates all authentication business logic."""

import json
from typing import Any, cast
from uuid import UUID

from jwt import ExpiredSignatureError
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.events import EventPriority
from src.app.core.security import (
    TokenType,
    blacklist_token,
    blacklist_tokens,
    get_password_hash,
    needs_rehash,
    verify_password,
    verify_token,
)
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.audit_logs import audit
from src.app.modules.auth.services.email_verification import EmailVerificationService
from src.app.modules.auth.services.lockout import LoginLockoutPolicy
from src.app.modules.auth.services.passwords import PasswordLifecycleService
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.modules.users import UserService
from src.app.shared.exceptions import ServiceUnavailableError, UnauthorizedError

logger = get_logger(__name__)
__all__ = ["AuthService", "get_auth_service"]


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

    def __init__(
        self,
        lockout_policy: LoginLockoutPolicy | None = None,
    ) -> None:
        self.lockout = lockout_policy or LoginLockoutPolicy()

    async def _resolve_user_auth_info(
        self,
        username_or_email: str,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        """Resolve user auth info by email or username with caching (PR-06 deduplication)."""
        user_service = UserService(db)
        if "@" in username_or_email:
            return await user_service.get_user_auth_info(email=username_or_email)
        return await user_service.get_user_auth_info(username=username_or_email)

    async def _revoke_session(
        self,
        access_token: str | None,
        user_data: Any,
        db: AsyncSession,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Invalidate the specific Redis session on logout.

        Returns (db_user, session_revoked) so callers can audit the
        revocation outcome without masking unexpected failures.
        """
        from redis.exceptions import RedisError
        from sqlalchemy.exc import SQLAlchemyError

        if not user_data or not access_token:
            return None, False
        db_user: dict[str, Any] | None = None
        try:
            db_user = await self._resolve_user_auth_info(user_data.username_or_email, db)
            if db_user and access_token:
                from src.app.core.security.jwt_utils import (
                    extract_claim_unverified,
                    extract_jti_unverified,
                )
                from src.app.core.security.session_manager import session_manager as global_session_manager

                session_id = (
                    extract_claim_unverified(access_token, "sid")
                    or extract_jti_unverified(access_token)
                    or access_token[:32]
                )
                await global_session_manager.invalidate_session(db_user["id"], session_id)
            return db_user, True
        except (SQLAlchemyError, RedisError, JWTError, AttributeError, KeyError) as e:
            logger.warning("failed_to_invalidate_session_on_logout", error=str(e))
            return db_user, False

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
        db_user = await self._resolve_user_auth_info(username_or_email, db)

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
                await UserService(db).update_password_hash(db_user["id"], await get_password_hash(password))
            except Exception as e:  # noqa: BLE001 - rehash is best-effort; never block login
                logger.warning("rehash_on_login_failed", user_id=db_user.get("id"), error=str(e))

        return cast(dict[str, Any], db_user)

    async def _publish_login_audit(
        self,
        request: Any = None,
        username: str = "",
        success: bool = False,
        tenant_id: UUID | None = None,
        user_id: int | None = None,
        reason: str | None = None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Publish audit log for login attempt."""
        resolved_ip = ip_address or (get_client_ip(request) if request else None)
        resolved_ua = user_agent or (request.headers.get("user-agent") if hasattr(request, "headers") else None)
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
                ip_address=resolved_ip,
                user_agent=resolved_ua,
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.error("failed_to_publish_audit_log", error=str(e))
            # SECURITY FIX: Make audit log failure mode configurable
            # fail_open: Log error but allow operation (default, availability focused)
            # fail_closed: Raise exception and block operation (security focused, compliance)
            # Note: fail_closed requires ServiceUnavailableException to be defined
            if getattr(settings.auth, "AUDIT_LOG_FAILURE_MODE", "fail_open") == "fail_closed":
                from src.app.shared.exceptions import ServiceUnavailableError

                raise ServiceUnavailableError("Service temporarily unavailable") from e

    async def login(
        self,
        username: str,
        password: str,
        response: Any = None,
        request: Any = None,
        db: AsyncSession | None = None,
        redis: Redis | None = None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
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
        if db is None:
            raise ServiceUnavailableError("Database session is required")
        if redis is None:
            raise ServiceUnavailableError("Redis client is required")
        # SECURITY FIX: Normalize username for consistent lockout tracking
        # Prevents bypass via case variations (e.g., User@example.com vs user@example.com)
        normalized_username = username.lower().strip()

        # SECURITY FIX: Check lockout BEFORE authentication to prevent CPU exhaustion (DoS)
        lockout = self.lockout
        if await lockout.is_locked(redis, normalized_username):
            # Avoid DB hit on locked path to prevent resource exhaustion (A-R1-03)
            await self._publish_login_audit(
                request, username, False, reason="account_locked", ip_address=ip_address, user_agent=user_agent
            )
            raise UnauthorizedError("Wrong username, email or password.")

        # Authenticate (this takes time due to bcrypt)
        # SECURITY FIX: Use normalized_username (already computed for lockout key)
        # to ensure the credential lookup and lockout tracking operate on the same form.
        user = await self.authenticate_user(username_or_email=normalized_username, password=password, db=db)

        if not user:
            await lockout.record_failed_attempt(redis, normalized_username)
            await self._publish_login_audit(
                request, username, False, reason="invalid_credentials", ip_address=ip_address, user_agent=user_agent
            )
            raise UnauthorizedError("Wrong username, email or password.")
        if not user.get("is_active", False):
            await self._publish_login_audit(
                request, username, False, reason="account_inactive", ip_address=ip_address, user_agent=user_agent
            )
            raise UnauthorizedError("User account is no longer active.")

        # Clear failed attempts on success (single roundtrip)
        await lockout.clear(redis, normalized_username)

        # Check if MFA is enabled for this user
        from src.app.modules.auth.services.mfa import get_mfa_service

        mfa_service = get_mfa_service()
        mfa_enabled = await mfa_service.is_mfa_enabled(user["id"], db)

        if mfa_enabled:
            client_ip = ip_address or (get_client_ip(request) if request else None)

            mfa_response = await TokenIssuer.issue_mfa_pending(user, redis=redis, ip_address=client_ip)

            # Return partial authentication response
            await self._publish_login_audit(
                request,
                username,
                True,
                tenant_id=user["tenant_id"],
                user_id=user["id"],
                ip_address=ip_address,
                user_agent=user_agent,
            )

            return mfa_response

        return await self._generate_auth_tokens(
            user, response, request, username, ip_address=ip_address, user_agent=user_agent
        )

    async def _generate_auth_tokens(
        self,
        user: dict[str, Any],
        response: Any = None,
        request: Any = None,
        username: str = "",
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
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
            subject=user["username"],
        )

        # Audit log success
        await self._publish_login_audit(
            request,
            username,
            True,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "access_token": issued.access_token,
            "token_type": "bearer",
            "refresh_token": issued.refresh_token,
        }

    async def _reject_reused_refresh_token(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Kill the token family if a rotated refresh token is replayed.

        Every successful rotation stores ``refresh_rotated:{old_jti}``. A
        token whose jti carries that marker was already rotated once, so
        presenting it again means the old token was stolen: invalidate the
        session, blacklist the replacement token, audit, and reject with the
        same message as an invalid token (no oracle).
        """
        from src.app.core.redis_provider import get_redis
        from src.app.core.security.jwt_utils import extract_jti_unverified

        redis_client = get_redis()
        if redis_client is None:
            return  # fail-closed downstream: verify/blacklist require Redis
        old_jti = extract_jti_unverified(refresh_token)
        if not old_jti:
            return  # unverifiable here; verify_token below rejects it
        try:
            raw = await redis_client.get(f"refresh_rotated:{old_jti}")
        except Exception as e:
            logger.debug("refresh reuse-marker lookup failed", error=str(e))
            return
        if not raw:
            return
        try:
            marker = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            user_id = marker["user_id"]
            session_id = marker["sid"]
            new_jti = marker["new_jti"]
            new_exp = float(marker["new_exp"])
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("malformed refresh rotation marker", jti=old_jti, error=str(e))
            return

        # Reuse confirmed — revoke the family best-effort, then reject.
        try:
            from datetime import UTC
            from datetime import datetime as dt

            from src.app.core.security.session_manager import session_manager as global_session_manager
            from src.app.core.security.token_blacklist_cache import add_token_to_blacklist

            if session_id:
                await global_session_manager.invalidate_session(user_id, session_id)
            await add_token_to_blacklist(f"jti:{new_jti}", dt.fromtimestamp(new_exp, tz=UTC))
        except Exception as e:
            logger.warning("refresh-reuse family revocation best-effort failed", error=str(e))
        try:
            await audit(
                action="auth.refresh_token_reuse_detected",
                entity_type="session",
                entity_id=str(session_id),
                tenant_id=None,
                user_id=user_id,
                details={"rotated_jti": old_jti, "revoked_jti": new_jti},
                ip_address=ip_address,
                user_agent=user_agent,
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            logger.error("failed to audit refresh token reuse", error=str(e))
        raise UnauthorizedError("Invalid refresh token.")

    async def refresh_access_token(
        self,
        refresh_token: str | None,
        response: Any = None,
        db: AsyncSession | None = None,
        request: Any = None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Refresh access token using refresh token.

        Implements rotation: invalidates old refresh token and issues a new one.
        Includes absolute session lifetime check to prevent indefinite refresh.
        """
        if db is None:
            raise ServiceUnavailableError("Database session is required")
        if not refresh_token:
            raise UnauthorizedError("Refresh token missing.")

        # P2-1: refresh-reuse detection — a rotated token presented again
        # means theft; revoke the whole family before any other check.
        await self._reject_reused_refresh_token(
            refresh_token, ip_address=ip_address, user_agent=user_agent
        )

        user_data = await verify_token(refresh_token, TokenType.REFRESH, db)
        if not user_data:
            raise UnauthorizedError("Invalid refresh token.")

        # SECURITY FIX: Check absolute session expiration to enforce maximum session lifetime
        # Use absolute_exp from already-verified token (avoid double JWT decode)
        absolute_exp = user_data.absolute_exp
        if absolute_exp:
            from datetime import UTC
            from datetime import datetime as dt

            if dt.fromtimestamp(absolute_exp, tz=UTC) < dt.now(UTC):
                raise UnauthorizedError(
                    "Session expired. Please log in again.",
                )

        # Fetch user with tier for Zero-DB rate limiting
        # Use UserService to benefit from auth info caching
        db_user = await self._resolve_user_auth_info(user_data.username_or_email, db)

        # A1 SECURITY FIX: Block token refresh for deleted, non-existent, or inactive users
        if not db_user or db_user.get("is_deleted", False) or not db_user.get("is_active", False):
            raise UnauthorizedError("User account is no longer active.")

        # A2/A3: recover the OLD refresh payload so rotation can carry forward
        # absolute_exp, original iat and the stable sid. The token string has
        # already been signature-verified by verify_token() above; this decode
        # is a local operation to surface claims TokenData does not expose.
        from src.app.core.security.jwt_utils import decode_jwt_token, extract_claim_unverified, extract_jti_unverified

        old_refresh_payload = await decode_jwt_token(refresh_token) or {}

        # P2-1: bind the refresh token to the user's tenant. Enforced only
        # when both sides carry a tenant (legacy tokens may lack the claim).
        payload_tenant = old_refresh_payload.get("tenant_id")
        user_tenant = db_user.get("tenant_id")
        if payload_tenant is not None and user_tenant is not None and str(payload_tenant) != str(user_tenant):
            raise UnauthorizedError("Invalid refresh token.")

        # SECURITY FIX: Check token version to enforce password change/all-session invalidation
        # `user_data.token_version` is pulled from the refresh token by `verify_token`
        current_token_version = db_user.get("token_version", 0)
        if hasattr(user_data, "token_version") and user_data.token_version != current_token_version:
            raise UnauthorizedError("Session invalidated due to password change or security event.")

        # Include tier info in JWT claims for Zero-DB rate limiting
        # SECURITY FIX: Include token_version for session invalidation
        # SECURITY FIX: Include tenant_id for tenant isolation during token refresh
        token_data = TokenIssuer.build_token_data(subject=user_data.username_or_email, user=db_user or {})

        # Create new tokens FIRST to ensure atomicity. If this fails, the user is not left locked out.
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        lock_key = None
        if redis_client and user_data.jti:
            lock_key = f"refresh_lock:{user_data.jti}"
            lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=30)
            if not lock_acquired:
                raise UnauthorizedError("Token refresh already in progress.")
        try:
            # A2/A3: rotate with carried session claims instead of minting fresh ones
            new_access_token, new_refresh_token = await TokenIssuer.rotate_token_pair(token_data, old_refresh_payload)

            # Blacklist the used refresh token (Rotation) AFTER new tokens are successfully generated.
            # Thread the already-verified payload: it is a verified decode of
            # this exact token string (verify_token() above + decode_jwt_token()
            # below both check the signature), so blacklist_token() reads
            # 'exp'/'jti' directly instead of performing a third verified decode.
            await blacklist_token(token=refresh_token, db=db, payload=old_refresh_payload)

            # P2-1: plant a rotation marker so a replayed old token is
            # detectable as theft (refresh-reuse → family revocation).
            try:
                from datetime import UTC
                from datetime import datetime as dt

                now_ts = dt.now(UTC).timestamp()
                marker_ttl = int(
                    min(
                        max(60, float(old_refresh_payload.get("exp", now_ts + 60)) - now_ts),
                        settings.crypt.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
                    )
                )
                # The new token was minted by this process (rotate_token_pair
                # above), so its signature is valid by construction — read jti
                # and exp with cheap unverified base64 extraction instead of
                # another verified decode.
                new_jti = extract_jti_unverified(new_refresh_token)
                new_exp_raw = extract_claim_unverified(new_refresh_token, "exp")
                new_exp = now_ts + marker_ttl
                if new_exp_raw is not None:
                    try:
                        new_exp = float(new_exp_raw)
                    except (TypeError, ValueError):
                        pass
                if redis_client and user_data.jti and new_jti:
                    await redis_client.setex(
                        f"refresh_rotated:{user_data.jti}",
                        marker_ttl,
                        json.dumps(
                            {
                                "user_id": db_user["id"],
                                "sid": user_data.sid,
                                "new_jti": new_jti,
                                "new_exp": new_exp,
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("failed to plant refresh rotation marker", error=str(e))
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

        # NOTE: refresh cookie ownership lives in the router (single owner)
        # to avoid double Set-Cookie on refresh; service only returns the token.
        from src.app.core.security.login_helpers import register_login_session

        await register_login_session(
            user_id=db_user["id"],
            access_token=new_access_token,
            request=request,
            verify=False,
            device_label=user_agent,
            # The carried sid is already in hand (rotation preserves it) —
            # no need to re-parse the minted access token to recover it.
            session_id=old_refresh_payload.get("sid") or user_data.sid,
        )

        return {
            "access_token": new_access_token,
            "token_type": "bearer",
            "refresh_token": new_refresh_token,
        }

    async def logout(
        self,
        access_token: str | None,
        refresh_token: str | None,
        response: Any = None,
        request: Any = None,
        db: AsyncSession | None = None,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
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
        dict[str, Any]
            Success message plus ``session_revoked`` flag so callers can
            distinguish full vs partial (tokens blacklisted, session
            registry untouched) logout. The HTTP layer exposes only
            ``message`` via LogoutResponse; direct callers see both keys.

        Raises
        ------
        UnauthorizedException
            If neither access nor refresh token is provided, or a token is invalid
        """
        if db is None:
            raise ServiceUnavailableError("Database session is required")
        if not access_token and not refresh_token:
            raise UnauthorizedError("No token provided.")

        user_data = None
        if access_token:
            try:
                user_data = await verify_token(access_token, TokenType.ACCESS, db)
            except (UnauthorizedError, ExpiredSignatureError):
                pass  # Token expired or invalid, proceed with logout anyway

        try:
            await blacklist_tokens(access_token=access_token, refresh_token=refresh_token, db=db)
        except JWTError as e:
            logger.error("logout_jwt_error", error=str(e))
            raise UnauthorizedError("Invalid token.") from e

        from src.app.core.security.login_helpers import clear_refresh_cookies

        if response is not None:
            clear_refresh_cookies(response)

        db_user, session_revoked = await self._revoke_session(access_token, user_data, db)

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
                    "session_revoked": session_revoked,
                },
                ip_address=ip_address or (get_client_ip(request) if request else None),
                user_agent=user_agent or (request.headers.get("user-agent") if hasattr(request, "headers") else None),
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.warning("failed_to_publish_logout_audit_log", error=str(e))

        return {"message": "Logged out successfully", "session_revoked": session_revoked}

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
