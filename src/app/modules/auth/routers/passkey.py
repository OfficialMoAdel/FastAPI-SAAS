from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException
from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.security.rate_policy import check_security_budget
from src.app.core.utils.cache import async_get_redis
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.auth.schemas import (
    LoginMFAResponse,
    LoginTokenResponse,
    PasskeyAuthenticationOptionsResponse,
    PasskeyCredentialList,
    PasskeyCredentialResponse,
    PasskeyLoginVerifyRequest,
    PasskeyRegistrationOptionsResponse,
    PasskeyRegistrationRequest,
    PasskeyRegistrationVerifyRequest,
)
from src.app.modules.auth.services.passkey import passkey_service

router = APIRouter(prefix="/auth/passkey", tags=["passkey"])


# ============================================================================
# Rate Limiting for Passkey Login (T082)
# ============================================================================

# Rate limit: the passkey_login budget in settings.rate_limit.SECURITY_BUDGETS
# (default 10/min per IP). The engine owns keys, algorithm, and fail-open policy.


async def passkey_login_rate_limit(request: Request) -> None:
    """IP-based rate limiting for passkey login endpoints.

    Budget: ``passkey_login`` in ``settings.rate_limit.SECURITY_BUDGETS``
    (default 10 requests per minute per IP address).
    """
    client_ip = get_client_ip(request)
    verdict = await check_security_budget("passkey_login", client_ip)

    if verdict.is_limited:
        raise RateLimitException("Too many login attempts. Please try again later.")


# ============================================================================
# Registration Endpoints
# ============================================================================


@router.post("/register", response_model=PasskeyRegistrationOptionsResponse)
async def register_begin(
    request: PasskeyRegistrationRequest,
    ctx: RequestContextDep,
    redis: Redis = Depends(async_get_redis),
):
    """Start passkey registration process (generate challenge)."""
    options, registration_id = await passkey_service.generate_registration_challenge(
        db=ctx.db,
        user=ctx.user,
        device_type=request.device_type,
        redis=redis,
    )
    return PasskeyRegistrationOptionsResponse(publicKey=options, registration_id=registration_id)


@router.post("/register/verify", response_model=PasskeyCredentialResponse)
async def register_complete(
    request: PasskeyRegistrationVerifyRequest,
    ctx: RequestContextDep,
    redis: Redis = Depends(async_get_redis),
):
    """Complete passkey registration (verify attestation)."""
    credential = await passkey_service.verify_registration(
        db=ctx.db,
        user=ctx.user,
        credential=request.credential,
        registration_id=request.registration_id,
        redis=redis,
    )
    return credential


@router.get("/credentials", response_model=PasskeyCredentialList)
async def list_credentials(
    ctx: RequestContextDep,
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
):
    """List user's passkey credentials (paginated)."""
    credentials = await passkey_service.list_credentials(db=ctx.db, user_id=ctx.user_id)
    start = pagination.offset
    page_items = credentials[start : start + pagination.limit]
    return build_paginated_response(
        items=page_items, total=len(credentials), page=pagination.page, per_page=pagination.per_page
    )


@router.delete("/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: int,
    ctx: RequestContextDep,
):
    """Delete a passkey credential."""
    await passkey_service.delete_credential(db=ctx.db, user_id=ctx.user_id, credential_id=credential_id)


# ============================================================================
# Authentication Endpoints (T081 - Passkey Login)
# ============================================================================


@router.post(
    "/login",
    response_model=PasskeyAuthenticationOptionsResponse,
    dependencies=[Depends(passkey_login_rate_limit)],
)
async def login_begin(
    redis: Redis = Depends(async_get_redis),
):
    """Start passkey authentication process (generate challenge).

    P2-A: Usernameless flow — no email hint is accepted, so the response is
    identical for every caller and cannot be used to enumerate accounts with
    registered passkeys. The authenticator offers its discoverable
    credentials; the user is resolved from the asserted credential at verify
    time.
    Rate limited: 10 requests per minute per IP.
    """
    options, challenge_id = await passkey_service.generate_authentication_challenge(
        redis=redis,
    )
    return PasskeyAuthenticationOptionsResponse(publicKey=options, challenge_id=challenge_id)


@router.post(
    "/login/verify",
    response_model=LoginTokenResponse | LoginMFAResponse,
    dependencies=[Depends(passkey_login_rate_limit)],
)
async def login_complete(
    request: PasskeyLoginVerifyRequest,
    response: Response,
    fastapi_request: Request,
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
        request=fastapi_request,
        response=response,
    )

    # Cookie written by the issuance seam; pop the refresh token so the
    # JSON body carries only the access token (M4 parity with SSO).
    result.pop("refresh_token", None)

    if result.get("mfa_required"):
        return LoginMFAResponse(**result)

    return LoginTokenResponse(**result)
