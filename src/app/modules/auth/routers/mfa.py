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

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.security.budgets import Identity, SecurityAction, check
from src.app.modules.auth.schemas import (
    MFABackupCodesResponse,
    MFACompleteLoginRequest,
    MFACompleteLoginResponse,
    MFADisableRequest,
    MFAEnableRequest,
    MFASetupResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    MFAVerifyResponse,
    StepUpRequest,
)
from src.app.modules.auth.services.mfa import MFAService, get_mfa_service
from src.app.modules.auth.services.step_up import verify_step_up_password
from src.app.shared.exceptions import (  # noqa: F401 — re-exported for test patch targets
    RateLimitExceededError,
    UnauthorizedError,
)

router = APIRouter(prefix="/mfa", tags=["mfa"])

# MFA meters live in settings.rate_limit.SECURITY_BUDGETS. The security-budget
# seam owns the leg set, keys, algorithm and fail-closed policy; a door only
# supplies the identity fact. The three verification meters are three separate
# doors — step-up, pending token, and the per-account backstop — and are never
# merged, because merging any two would couple two independent limits.


async def check_mfa_mutate_rate_limit(
    request: Request,
    user_id: int | str,
) -> None:
    """Rate-limit MFA state mutations (disable, backup-code regeneration).

    P1-5: these endpoints verify password (+TOTP/backup code) inside the
    service with no attempt cap — an authenticated session (or stolen token)
    could brute-force the password or second factor indefinitely. Keyed on
    user_id: the caller is authenticated, so IP-keying would let an attacker
    rotate IPs to dodge the limit.
    """
    await check(SecurityAction.MFA_MUTATE, Identity(user_id=user_id))


@router.post("/setup", response_model=MFASetupResponse)
async def setup_mfa(
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
    step_up: StepUpRequest,
) -> MFASetupResponse:
    """Generate TOTP secret for MFA setup.

    Returns a secret key and provisioning URI for QR code generation.
    The secret must be stored temporarily by the client and submitted
    with the verification token to enable MFA.

    Returns:
        MFASetupResponse with secret and provisioning URI.
    """
    # P1 FIX (code-review): setup/enable had no rate limit while verify did —
    # the enable TOTP check was brute-forceable. Throttle both seams.
    await check_mfa_mutate_rate_limit(fastapi_request, ctx.user_id)
    # Run-3: enrolling a second factor requires step-up re-authentication, not
    # merely a valid session. Matches disable_mfa's existing bar.
    await verify_step_up_password(db=ctx.db, user_id=ctx.user_id, password=step_up.password)
    result = await service.generate_totp_secret(
        user_id=ctx.user_id,
        db=ctx.db,
    )
    return MFASetupResponse(**result)


@router.post("/enable", response_model=dict[str, Any])
async def enable_mfa(
    request: MFAEnableRequest,
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
) -> dict[str, Any]:
    """Enable MFA for the current user.

    Verifies the provided TOTP token against the secret, then enables MFA.
    Returns backup codes that should be saved securely by the user.

    Args:
        request: Enable request containing secret and verification token.

    Returns:
        Dict with status and backup codes.

    Raises:
        ValidationError: If MFA already enabled or token is invalid.
    """
    await check_mfa_mutate_rate_limit(fastapi_request, ctx.user_id)
    # Run-3: re-verify at enable time. /mfa/setup now requires step-up, but a
    # setup_token obtained earlier must not be redeemable without the password.
    await verify_step_up_password(db=ctx.db, user_id=ctx.user_id, password=request.password)
    return await service.enable_mfa(
        user_id=ctx.user_id,
        request=request,
        db=ctx.db,
    )


@router.post("/verify", response_model=MFAVerifyResponse)
async def verify_mfa(
    request: MFAVerifyRequest,
    ctx: RequestContextDep,
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
        RateLimitExceededError: If too many verification attempts.
        UnauthorizedError: If token is invalid or MFA not enabled.
    """
    # Step-up verification meter: this door is authenticated, so the account
    # is the identity. Distinct from the complete-login door's two meters.
    await check(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id=ctx.user_id))

    result = await service.verify_totp_token(
        user_id=ctx.user_id,
        request=request,
        db=ctx.db,
    )
    return MFAVerifyResponse(**result)


@router.post("/disable", response_model=dict[str, str])
async def disable_mfa(
    request: MFADisableRequest,
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
) -> dict[str, str]:
    """Disable MFA for the current user.

    Requires a valid MFA token for security verification.
    Rate limited to 5 mutation attempts per minute per user (P1-5).

    Args:
        request: Disable request containing verification token.

    Returns:
        Dict with success message.

    Raises:
        RateLimitExceededError: If too many mutation attempts.
        UnauthorizedError: If verification fails.
    """
    # P1-5: cap password/second-factor guessing behind an authenticated session.
    await check_mfa_mutate_rate_limit(fastapi_request, ctx.user_id)

    return await service.disable_mfa(
        user_id=ctx.user_id,
        password=request.password,
        totp_token=request.totp_token,
        backup_code=request.backup_code,
        db=ctx.db,
    )


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
) -> MFAStatusResponse:
    """Get MFA status for the current user.

    Returns information about whether MFA is enabled and
    the number of remaining backup codes.

    Returns:
        MFAStatusResponse with MFA status information.
    """
    result = await service.get_mfa_status(
        user_id=ctx.user_id,
        db=ctx.db,
    )
    return MFAStatusResponse(**result)


@router.post("/backup-codes", response_model=MFABackupCodesResponse)
async def regenerate_backup_codes(
    request: MFADisableRequest,  # Reuse disable request schema (password + optional totp)
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
) -> MFABackupCodesResponse:
    """Generate new backup codes.

    Replaces existing backup codes with a new set.
    Requires password verification. Optionally accepts TOTP token
    for additional security.
    Rate limited to 5 mutation attempts per minute per user (P1-5).

    Args:
        request: Request containing password and optional TOTP token.

    Returns:
        MFABackupCodesResponse with new backup codes.

    Raises:
        RateLimitExceededError: If too many mutation attempts.
        UnauthorizedError: If verification fails.
    """
    # P1-5: cap password/second-factor guessing behind an authenticated session.
    await check_mfa_mutate_rate_limit(fastapi_request, ctx.user_id)

    result = await service.regenerate_backup_codes(
        user_id=ctx.user_id,
        password=request.password,
        totp_token=request.totp_token,
        backup_code=request.backup_code,
        db=ctx.db,
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
        RateLimitExceededError: If too many verification attempts.
        UnauthorizedError: If MFA pending token or code is invalid.
    """
    issued = await service.complete_login(request, db, fastapi_request=fastapi_request, response=response)
    return MFACompleteLoginResponse(
        access_token=issued.access_token,
        token_type="bearer",
        message="Login successful",
    )
