"""MFA router - handles multi-factor authentication endpoints.

Provides API endpoints for:
- TOTP MFA setup and enablement
- MFA token verification
- MFA disablement
- Backup code management
- MFA login completion
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import RateLimitException, UnauthorizedException
from src.app.core.security import get_mfa_pending_token_manager
from src.app.core.utils import cache as _redis_cache
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.auth.services.tokens import TokenIssuer
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
        # SECURITY FIX: Use get_client_ip() consistently instead of raw XFF parsing.
        # Raw X-Forwarded-For leftmost entry is attacker-controlled.
        client_ip = get_client_ip(request)
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
    current_user: Annotated[Any, Depends(get_current_user)],
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
        user_id=get_user_attr(current_user, "id"),
        db=db,
    )
    return MFASetupResponse(**result)


@router.post("/enable", response_model=dict[str, Any])
async def enable_mfa(
    request: MFAEnableRequest,
    current_user: Annotated[Any, Depends(get_current_user)],
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
        user_id=get_user_attr(current_user, "id"),
        request=request,
        db=db,
    )


@router.post("/verify", response_model=MFAVerifyResponse)
async def verify_mfa(
    request: MFAVerifyRequest,
    current_user: Annotated[Any, Depends(get_current_user)],
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
    await check_mfa_rate_limit(fastapi_request, identifier=str(get_user_attr(current_user, "id")))

    result = await service.verify_totp_token(
        user_id=get_user_attr(current_user, "id"),
        request=request,
        db=db,
    )
    return MFAVerifyResponse(**result)


@router.post("/disable", response_model=dict[str, str])
async def disable_mfa(
    request: MFADisableRequest,
    current_user: Annotated[Any, Depends(get_current_user)],
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
        user_id=get_user_attr(current_user, "id"),
        password=request.password,
        totp_token=request.totp_token,
        backup_code=request.backup_code,
        db=db,
    )


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: Annotated[Any, Depends(get_current_user)],
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
        user_id=get_user_attr(current_user, "id"),
        db=db,
    )
    return MFAStatusResponse(**result)


@router.post("/backup-codes", response_model=MFABackupCodesResponse)
async def regenerate_backup_codes(
    request: MFADisableRequest,  # Reuse disable request schema (password + optional totp)
    current_user: Annotated[Any, Depends(get_current_user)],
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
        user_id=get_user_attr(current_user, "id"),
        password=request.password,
        totp_token=request.totp_token,
        backup_code=request.backup_code,
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
    # Use live cache.client (refreshed by tests per-loop)
    if not _redis_cache.client:
        raise UnauthorizedException("Authentication service unavailable")

    from src.app.core.utils.ip_utils import get_client_ip

    client_ip = get_client_ip(fastapi_request)

    token_store = get_mfa_pending_token_manager(_redis_cache.client)
    token_data = await token_store.consume(request.mfa_token)

    if not token_data or token_data.get("scope") != "mfa_pending":
        raise UnauthorizedException("Invalid or expired MFA pending token")

    stored_ip = token_data.get("ip_address")
    if stored_ip and stored_ip != client_ip:
        from src.app.core.logger import get_logger
        logger = get_logger(__name__)
        logger.warning(
            "MFA IP mismatch",
            stored_ip=stored_ip,
            client_ip=client_ip,
            user_id=token_data.get("user_id")
        )
        # SECURITY FIX (M-1): Optionally reject IP mismatch to prevent stolen MFA tokens
        if getattr(settings.auth, "MFA_ENFORCE_IP_BINDING", False):
            raise UnauthorizedException(
                "MFA verification rejected: IP address changed since login. Please try again."
            )

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

    issued = await TokenIssuer.issue_session(
        user,
        channel="mfa",
        request=fastapi_request,
        response=response,
        subject=user["username"],
        verify=False,  # MFA completion: session registered from unverified jti extraction
    )
    return MFACompleteLoginResponse(
        access_token=issued.access_token,
        token_type="bearer",
        message="Login successful",
    )
