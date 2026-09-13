"""Shared helpers for post-authentication actions across auth modules."""

from typing import Any

import redis
from fastapi import Request, Response
from structlog import get_logger

from src.app.core.config import EnvironmentOption, settings
from src.app.core.json_utils import json_dumps
from src.app.core.security.jwt_utils import (
    decode_jwt_token,
    extract_claim_unverified,
    extract_jti_unverified,
    validate_jwt_length,
)
from src.app.core.security.session_manager import session_manager as global_session_manager
from src.app.core.utils.ip_utils import get_client_ip

logger = get_logger(__name__)


# R0-05: refresh cookie naming and attributes.
#
# Staging/production use the ``__Host-`` prefix: browsers enforce Secure +
# Path=/ + no Domain for it, which pins the cookie to the exact host and
# blocks subdomain/origin hijack. ``__Host-`` cookies are rejected over
# plain HTTP, so local development keeps the legacy name with Secure off —
# every other environment is Secure + Strict unconditionally.
LEGACY_REFRESH_COOKIE_NAME = "refresh_token"


def _refresh_cookie_name() -> str:
    """Return the refresh cookie name for the current environment."""
    if settings.env.ENVIRONMENT == EnvironmentOption.LOCAL:
        return LEGACY_REFRESH_COOKIE_NAME
    return "__Host-refresh_token"


def get_refresh_cookie(request: Request) -> str | None:
    """Read the refresh token, accepting the legacy name for continuity.

    After an environment upgrade, existing sessions still carry the legacy
    cookie; accepting it (and re-issuing under the new name on refresh)
    avoids forcing a global re-login.
    """
    return request.cookies.get(_refresh_cookie_name()) or request.cookies.get(LEGACY_REFRESH_COOKIE_NAME)


def clear_refresh_cookies(response: Response) -> None:
    """Delete both current and legacy refresh cookie names."""
    is_local = settings.env.ENVIRONMENT == EnvironmentOption.LOCAL
    # __Host- cookies require Secure + Path=/ + no Domain — browsers reject
    # a deletion Set-Cookie without Secure, so mirror set_refresh_cookie attrs.
    response.delete_cookie(key=_refresh_cookie_name(), secure=not is_local, samesite="strict", httponly=True)  # type: ignore[call-arg]
    if _refresh_cookie_name() != LEGACY_REFRESH_COOKIE_NAME:
        response.delete_cookie(key=LEGACY_REFRESH_COOKIE_NAME, secure=not is_local, samesite="strict", httponly=True)  # type: ignore[call-arg]


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Set refresh token as httpOnly cookie with hardened security settings.

    R0-05: ``Secure`` and ``SameSite=strict`` in every environment except
    plain-HTTP local dev (Secure cookies are not sent over http); staging
    and production use the ``__Host-`` prefix.

    Args:
        response: FastAPI response object.
        refresh_token: The refresh token to set in the cookie.
    """
    max_age = settings.crypt.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    is_local = settings.env.ENVIRONMENT == EnvironmentOption.LOCAL

    response.set_cookie(
        key=_refresh_cookie_name(),
        value=refresh_token,
        httponly=True,
        secure=not is_local,
        samesite="strict",
        max_age=max_age,
    )


async def register_login_session(
    user_id: int,
    access_token: str,
    request: Request | None = None,
    verify: bool = True,
    device_label: str | None = None,
    session_id: str | None = None,
) -> None:
    """Register session in Redis with device info.

    Failure handling is governed by ``settings.auth.SESSION_REGISTRATION_FAIL_MODE``
    (S-4): ``fail_closed`` (default) raises HTTP 503 so concurrent-session
    limiting is never silently bypassed; ``fail_open`` logs and lets the login
    proceed. Covers both failure signals from ``register_session`` — a raised
    ``RedisError`` and a ``{"registered": False}`` result (Redis unconfigured
    or Lua registration error).

    Args:
        user_id: The ID of the user logging in.
        access_token: The issued access token.
        verify: If True, uses verified jwt decoding. If False, extracts unverified.
            (e.g. "Magic Link Login"). Defaults to the generic fallback.
        session_id: Optional already-known session id (the stable ``sid``
            claim). When provided, the token is not parsed at all — used by
            callers that already hold the sid (rotation carry, issue_session).
    """
    try:
        if session_id is not None:
            resolved_session_id = session_id
        elif verify:
            # SECURITY: Validate token length and verify signature for session registration
            if not validate_jwt_length(access_token):
                logger.warning("JWT decode blocked: token too long")
                decoded_token: dict[str, Any] = {}
            else:
                decoded_token = await decode_jwt_token(access_token) or {}
            # A3: sessions are keyed by the stable sid claim (shared by the
            # access and refresh tokens of one login); fall back to jti for
            # tokens minted before sid existed.
            resolved_session_id = decoded_token.get("sid") or decoded_token.get("jti", access_token[:32])
        else:
            resolved_session_id = extract_claim_unverified(access_token, "sid") or (
                extract_jti_unverified(access_token) or access_token[:32]
            )

        client_ip = get_client_ip(request) if request else "Unknown"

        user_agent = request.headers.get("user-agent", "Unknown") if request else (device_label or "Unknown")

        device_info_data = {
            "user_agent": user_agent,
            "ip": client_ip,
        }
        device_info_str = json_dumps(device_info_data, as_str=True)

        result = await global_session_manager.register_session(
            user_id=user_id,
            session_id=resolved_session_id,
            device_info=device_info_str,
        )
        # S-4: register_session signals failure two ways — raising RedisError
        # (handled below) OR returning {"registered": False} when Redis is
        # unconfigured or the Lua registration errored. Both must honor the
        # configured fail mode, otherwise fail_closed silently fails open.
        if not result.get("registered", True):
            logger.warning("session_registration_failed", reason=result.get("reason"))
            _enforce_session_registration_fail_mode()
    except redis.exceptions.RedisError as e:
        logger.warning("session_registration_failed", error=str(e))
        _enforce_session_registration_fail_mode()


def _enforce_session_registration_fail_mode() -> None:
    """S-4: apply SESSION_REGISTRATION_FAIL_MODE after a registration failure.

    fail_closed rejects the login (503) so concurrent-session limiting is never
    silently bypassed; fail_open logs and lets the login proceed.
    """
    if settings.auth.SESSION_REGISTRATION_FAIL_MODE == "fail_closed":
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable")
