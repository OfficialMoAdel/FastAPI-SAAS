"""MFA router - handles multi-factor authentication endpoints.

Provides API endpoints for:
- TOTP MFA setup and enablement
- MFA token verification
- MFA disablement
- Backup code management
- MFA login completion
"""

import asyncio
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException, UnauthorizedException
from src.app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    MFAPendingTokenManager,
    create_access_token,
    create_refresh_token,
)
from src.app.core.utils import cache as _redis_cache
from src.app.core.utils.cache import client as redis_client
from src.app.core.utils.rate_limit import rate_limiter
from src.app.modules.mfa.schemas import (
    MFABackupCodesResponse,
    MFACompleteLoginRequest,
    MFACompleteLoginResponse,
    MFADisableRequest,
    MFAEnableRequest,
    MFASetupResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    MFAVerifyResponse,
)
from src.app.modules.mfa.service import MFAService, get_mfa_service
from src.app.modules.users.service import get_user_service

router = APIRouter(prefix="/mfa", tags=["mfa"])

# MFA verify rate limiting constants
MFA_VERIFY_LIMIT = 5  # attempts per minute
MFA_VERIFY_PERIOD = 60  # seconds (1 minute)


async def check_mfa_rate_limit(
    request: Request,
    identifier: str | None = None,
) -> None:
    """Check rate limit for MFA verification attempts.

    Uses Redis-based rate limiting with 5 attempts per minute.

    Args:
        request: FastAPI request object.
        identifier: Optional identifier (user_id, IP, etc.) for rate limiting.

    Raises:
        RateLimitException: If rate limit is exceeded.
    """
    # Use provided identifier or fall back to client IP
    if identifier is None:
        # Get client IP, handling proxies
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"
        identifier = client_ip

    # Build rate limit key
    key = f"mfa_verify:{identifier}"

    # Check rate limit
    is_limited, current_count = await rate_limiter.check_rate_limit(
        key, limit=MFA_VERIFY_LIMIT, period=MFA_VERIFY_PERIOD
    )

    if is_limited:
        raise RateLimitException(
            f"Too many MFA verification attempts. Please try again in {MFA_VERIFY_PERIOD} seconds."
        )


@router.post("/setup", response_model=MFASetupResponse)
async def setup_mfa(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> MFASetupResponse:
    """Generate TOTP secret for MFA setup.

    Returns a secret key and provisioning URI for QR code generation.
    The secret must be stored temporarily by the client and submitted
    with the verification token to enable MFA.

    Returns:
        MFASetupResponse with secret and provisioning URI.
    """
    result = await service.generate_totp_secret(
        user_id=current_user["id"],
        db=db,
    )
    return MFASetupResponse(**result)


@router.post("/enable", response_model=dict[str, Any])
async def enable_mfa(
    request: MFAEnableRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> dict[str, Any]:
    """Enable MFA for the current user.

    Verifies the provided TOTP token against the secret, then enables MFA.
    Returns backup codes that should be saved securely by the user.

    Args:
        request: Enable request containing secret and verification token.

    Returns:
        Dict with status and backup codes.

    Raises:
        BadRequestException: If MFA already enabled or token is invalid.
    """
    return await service.enable_mfa(
        user_id=current_user["id"],
        request=request,
        db=db,
    )


@router.post("/verify", response_model=MFAVerifyResponse)
async def verify_mfa(
    request: MFAVerifyRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
) -> MFAVerifyResponse:
    """Verify an MFA token.

    Validates the provided TOTP token or backup code.
    Rate limited to 5 attempts per minute per user.

    Args:
        request: Verify request containing the token to validate.

    Returns:
        MFAVerifyResponse with verification status.

    Raises:
        RateLimitException: If too many verification attempts.
        UnauthorizedException: If token is invalid or MFA not enabled.
    """
    # Check rate limit
    await check_mfa_rate_limit(fastapi_request, identifier=str(current_user["id"]))

    result = await service.verify_totp_token(
        user_id=current_user["id"],
        request=request,
        db=db,
    )
    return MFAVerifyResponse(**result)


@router.post("/disable", response_model=dict[str, str])
async def disable_mfa(
    request: MFADisableRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> dict[str, str]:
    """Disable MFA for the current user.

    Requires a valid MFA token for security verification.

    Args:
        request: Disable request containing verification token.

    Returns:
        Dict with success message.

    Raises:
        BadRequestException: If MFA not enabled.
        UnauthorizedException: If verification fails.
    """
    return await service.disable_mfa(
        user_id=current_user["id"],
        password=request.password,
        totp_token=request.totp_token,
        db=db,
    )


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> MFAStatusResponse:
    """Get MFA status for the current user.

    Returns information about whether MFA is enabled and
    the number of remaining backup codes.

    Returns:
        MFAStatusResponse with MFA status information.
    """
    result = await service.get_mfa_status(
        user_id=current_user["id"],
        db=db,
    )
    return MFAStatusResponse(**result)


@router.post("/backup-codes", response_model=MFABackupCodesResponse)
async def regenerate_backup_codes(
    request: MFADisableRequest,  # Reuse disable request schema (password + optional totp)
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> MFABackupCodesResponse:
    """Generate new backup codes.

    Replaces existing backup codes with a new set.
    Requires password verification. Optionally accepts TOTP token
    for additional security.

    Args:
        request: Request containing password and optional TOTP token.

    Returns:
        MFABackupCodesResponse with new backup codes.

    Raises:
        BadRequestException: If MFA not enabled.
        UnauthorizedException: If verification fails.
    """
    result = await service.regenerate_backup_codes(
        user_id=current_user["id"],
        password=request.password,
        totp_token=request.totp_token,
        db=db,
    )
    return MFABackupCodesResponse(**result)


@router.post("/complete-login", response_model=MFACompleteLoginResponse)
async def complete_mfa_login(
    request: MFACompleteLoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
) -> MFACompleteLoginResponse:
    """Complete MFA login flow.

    Validates the MFA pending token and MFA code (TOTP or backup code),
    then returns full access tokens.

    Args:
        request: Login completion request with MFA pending token and code.
        response: FastAPI response object for setting cookies.
        db: Database session.
        service: MFA service instance.

    Returns:
        MFACompleteLoginResponse with access token and user info.

    Raises:
        RateLimitException: If too many verification attempts.
        UnauthorizedException: If MFA pending token or code is invalid.
    """
    # Check rate limit (use MFA pending token as identifier)
    await check_mfa_rate_limit(fastapi_request, identifier=request.mfa_token)

    # Validate MFA pending token
    # Use module-level redis_client (patchable by tests), with fallback to live cache.client
    _redis_client = redis_client if redis_client is not None else _redis_cache.client
    if not _redis_client:
        raise UnauthorizedException("Authentication service unavailable")

    token_manager = MFAPendingTokenManager(_redis_client)
    token_data = await token_manager.validate_token(request.mfa_token)

    if not token_data:
        raise UnauthorizedException("Invalid or expired MFA pending token")

    user_id = token_data.get("user_id")
    username = token_data.get("username")

    if not user_id or not username:
        raise UnauthorizedException("Invalid MFA pending token")

    # Verify MFA code
    verify_request = MFAVerifyRequest(
        totp_token=request.totp_token,
        backup_code=request.backup_code,
    )
    verify_result = await service.verify_totp_token(
        user_id=user_id,
        request=verify_request,
        db=db,
    )

    if not verify_result.get("valid"):
        raise UnauthorizedException("Invalid MFA code")

    # Get user info for token generation
    user_service = get_user_service(db)
    user = await user_service.get_user_auth_info(username=username)

    if not user or user.get("is_deleted") or not user.get("is_active", False):
        raise UnauthorizedException("User account is no longer active")

    tier = user.get("tier")
    tier_name = tier.get("name") if tier else None
    token_data_payload = {
        "sub": user["username"],
        "tier_id": user.get("tier_id"),
        "tier_name": tier_name,
        "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,  # SECURITY FIX: include tenant_id
        "token_version": user.get("token_version", 0),  # M-06: Include for session invalidation
    }

    # Generate tokens
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token, refresh_token = await asyncio.gather(
        create_access_token(data=token_data_payload, expires_delta=access_token_expires),
        create_refresh_token(data=token_data_payload),
    )

    # Set refresh token cookie
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

    return MFACompleteLoginResponse(
        access_token=access_token,
        token_type="bearer",
        message="Login successful",
    )
