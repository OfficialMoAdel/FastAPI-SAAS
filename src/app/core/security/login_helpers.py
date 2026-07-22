"""Shared helpers for post-authentication actions across auth modules."""

import json
from typing import Any

import redis
from fastapi import Request, Response
from structlog import get_logger

from src.app.core.config import EnvironmentOption, settings
from src.app.core.security.jwt_utils import decode_jwt_token, extract_jti_unverified, validate_jwt_length
from src.app.core.security.session_manager import session_manager as global_session_manager
from src.app.core.utils.ip_utils import get_client_ip

logger = get_logger(__name__)


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Set refresh token as httpOnly cookie with standard security settings.

    Args:
        response: FastAPI response object.
        refresh_token: The refresh token to set in the cookie.
    """
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="strict" if is_production else "lax",
        max_age=max_age,
    )


async def register_login_session(
    user_id: int,
    access_token: str,
    request: Request | None = None,
    verify: bool = True
) -> None:
    """Register session in Redis with device info. Fail-safe (logs on error).

    Args:
        user_id: The ID of the user logging in.
        access_token: The issued access token.
        request: Optional FastAPI request object for client IP and User-Agent.
        verify: If True, uses verified jwt decoding. If False, extracts unverified.
    """
    try:
        if verify:
            # SECURITY: Validate token length and verify signature for session registration
            if not validate_jwt_length(access_token):
                logger.warning("JWT decode blocked: token too long")
                decoded_token: dict[str, Any] = {}
            else:
                decoded_token = await decode_jwt_token(access_token) or {}
            session_id = decoded_token.get("jti", access_token[:32])
        else:
            session_id = extract_jti_unverified(access_token) or access_token[:32]

        client_ip = get_client_ip(request) if request else "Unknown"

        device_info_data = {
            "user_agent": request.headers.get("user-agent", "Unknown") if request else "Unknown",
            "ip": client_ip,
        }
        device_info_str = json.dumps(device_info_data)

        await global_session_manager.register_session(
            user_id=user_id,
            session_id=session_id,
            device_info=device_info_str,
        )
    except redis.exceptions.RedisError as e:
        logger.warning("session_registration_failed", error=str(e))
