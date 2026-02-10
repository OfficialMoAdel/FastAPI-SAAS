from typing import Any

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.utils.cache import async_get_redis
from src.app.core.utils.rate_limit import rate_limiter
from src.app.modules.auth.schemas import AuthTokenResponse
from src.app.modules.passkey.schemas import (
    PasskeyAuthenticationOptionsResponse,
    PasskeyCredentialResponse,
    PasskeyDeleteResponse,
    PasskeyLoginRequest,
    PasskeyLoginVerifyRequest,
    PasskeyRegistrationOptionsResponse,
    PasskeyRegistrationRequest,
    PasskeyRegistrationVerifyRequest,
)
from src.app.modules.passkey.service import passkey_service

router = APIRouter(prefix="/auth/passkey", tags=["passkey"])


# ============================================================================
# Rate Limiting for Passkey Login (T082)
# ============================================================================

# Rate limit: 10 requests per minute per IP
PASSKEY_LOGIN_RATE_LIMIT = 10
PASSKEY_LOGIN_RATE_PERIOD = 60  # seconds


async def passkey_login_rate_limit(request: Request) -> None:
    """IP-based rate limiting for passkey login endpoints.

    Limits: 10 requests per minute per IP address.
    """
    client_ip = request.client.host if request.client else "unknown"
    key = f"passkey:login:{client_ip}"

    is_limited, current_count = await rate_limiter.check_rate_limit(
        key=key,
        limit=PASSKEY_LOGIN_RATE_LIMIT,
        period=PASSKEY_LOGIN_RATE_PERIOD,
    )

    if is_limited:
        raise RateLimitException("Too many login attempts. Please try again later.")


# ============================================================================
# Registration Endpoints
# ============================================================================


@router.post("/register", response_model=PasskeyRegistrationOptionsResponse)
async def register_begin(
    request: PasskeyRegistrationRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    redis: Redis = Depends(async_get_redis),
):
    """Start passkey registration process (generate challenge)."""
    options = await passkey_service.generate_registration_challenge(
        db=db,
        user=current_user,
        device_type=request.device_type,
        redis=redis,
    )
    return PasskeyRegistrationOptionsResponse(publicKey=options)


@router.post("/register/verify", response_model=PasskeyCredentialResponse)
async def register_complete(
    request: PasskeyRegistrationVerifyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    redis: Redis = Depends(async_get_redis),
):
    """Complete passkey registration (verify attestation)."""
    credential = await passkey_service.verify_registration(
        db=db,
        user=current_user,
        credential=request.credential,
        redis=redis,
    )
    return credential


@router.get("/credentials", response_model=list[PasskeyCredentialResponse])
async def list_credentials(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """List user's passkey credentials."""
    return await passkey_service.list_credentials(db=db, user_id=current_user["id"])


@router.delete("/credentials/{credential_id}", response_model=PasskeyDeleteResponse)
async def delete_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """Delete a passkey credential."""
    await passkey_service.delete_credential(db=db, user_id=current_user["id"], credential_id=credential_id)
    return PasskeyDeleteResponse(message="Credential deleted successfully")


# ============================================================================
# Authentication Endpoints (T081 - Passkey Login)
# ============================================================================


@router.post(
    "/login",
    response_model=PasskeyAuthenticationOptionsResponse,
    dependencies=[Depends(passkey_login_rate_limit)],
)
async def login_begin(
    request: PasskeyLoginRequest | None = None,
    db: AsyncSession = Depends(async_get_db),
    redis: Redis = Depends(async_get_redis),
):
    """Start passkey authentication process (generate challenge).

    Optionally accepts an email hint for discoverable credentials.
    Rate limited: 10 requests per minute per IP.
    """
    email = request.email if request else None
    options, challenge_id = await passkey_service.generate_authentication_challenge(
        db=db,
        redis=redis,
        email=email,
    )
    return PasskeyAuthenticationOptionsResponse(publicKey=options, challenge_id=challenge_id)


@router.post(
    "/login/verify",
    response_model=AuthTokenResponse,
    dependencies=[Depends(passkey_login_rate_limit)],
)
async def login_complete(
    request: PasskeyLoginVerifyRequest,
    db: AsyncSession = Depends(async_get_db),
    redis: Redis = Depends(async_get_redis),
):
    """Complete passkey authentication (verify assertion).

    Returns JWT tokens upon successful authentication.
    Rate limited: 10 requests per minute per IP.
    """
    result = await passkey_service.verify_authentication(
        db=db,
        redis=redis,
        credential=request.credential,
        challenge_id=request.challenge_id,
    )
    return AuthTokenResponse(**result)
