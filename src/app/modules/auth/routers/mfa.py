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
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.redis_provider import get_redis
from src.app.core.security import get_mfa_pending_token_manager
from src.app.core.security.rate_policy import check_security_budget
from src.app.core.utils.ip_utils import get_client_ip
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
)
from src.app.modules.auth.services.mfa import MFAService, get_mfa_service
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.modules.users import get_user_service
from src.app.shared.exceptions import (  # noqa: F401 — re-exported for test patch targets
    RateLimitExceededError,
    UnauthorizedError,
)

router = APIRouter(prefix="/mfa", tags=["mfa"])

# MFA budgets live in settings.rate_limit.SECURITY_BUDGETS (mfa_verify /
# mfa_mutate). The engine (check_security_budget) owns keys, algorithm, and
# fail-open policy; these aliases only document the budget names.
MFA_VERIFY_BUDGET = "mfa_verify"
MFA_MUTATE_BUDGET = "mfa_mutate"


async def check_mfa_rate_limit(
    request: Request,
    identifier: str | None = None,
    budget: str = MFA_VERIFY_BUDGET,
) -> None:
    """Check the MFA security budget for verification/mutation attempts.

    Limits live in ``settings.rate_limit.SECURITY_BUDGETS`` (default 5/min
    per budget). The engine owns keys, algorithm, and fail-open policy.

    Args:
        request: FastAPI request object.
        identifier: Optional identifier (user_id, IP, etc.) for rate limiting.
        budget: Budget name — ``mfa_verify`` or ``mfa_mutate``. P1-5 keeps
            them as distinct budgets so a user burning verify attempts
            cannot starve — or be starved by — mutation attempts.

    Raises:
        RateLimitExceededError: If rate limit is exceeded.
    """
    # Use provided identifier or fall back to client IP
    if identifier is None:
        # SECURITY FIX: Use get_client_ip() consistently instead of raw XFF parsing.
        # Raw X-Forwarded-For leftmost entry is attacker-controlled.
        identifier = get_client_ip(request)

    verdict = await check_security_budget(budget, identifier)

    if verdict.is_limited:
        raise RateLimitExceededError(
            f"Too many MFA verification attempts. Please try again in {verdict.period} seconds.",
            retry_after=verdict.retry_after or verdict.period,
        )


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
    await check_mfa_rate_limit(
        request,
        identifier=str(user_id),
        budget=MFA_MUTATE_BUDGET,
    )


@router.post("/setup", response_model=MFASetupResponse)
async def setup_mfa(
    ctx: RequestContextDep,
    service: Annotated[MFAService, Depends(get_mfa_service)],
    fastapi_request: Request,
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
    # Check rate limit
    await check_mfa_rate_limit(fastapi_request, identifier=str(ctx.user_id))

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
    # Check rate limit (use MFA pending token as identifier)
    await check_mfa_rate_limit(fastapi_request, identifier=request.mfa_token)

    # Validate MFA pending token
    # Use the live seam (refreshed by tests per-loop)
    client = get_redis()
    if not client:
        raise UnauthorizedError("Authentication service unavailable")

    from src.app.core.utils.ip_utils import get_client_ip

    client_ip = get_client_ip(fastapi_request)

    token_store = get_mfa_pending_token_manager(client)
    # R1-1: peek first, verify TOTP, consume only on success — a typo'd code
    # must not destroy the 5-minute pending token (previously consume ran
    # before verification, forcing a full password re-login on any mistake).
    # Double-submit is still single-flighted: the TOTP replay marker rejects
    # a reused code, and the consume below rejects a reused pending token.
    token_data = await token_store.peek(request.mfa_token)

    if not token_data or token_data.get("scope") != "mfa_pending":
        raise UnauthorizedError("Invalid or expired MFA pending token")

    # S-M3: read the token's remaining TTL while it still exists — it is the
    # clock for the IP-rotation grace window below (the common same-IP path
    # discards it and pays no extra RTT beyond this single read).
    remaining_ttl = await token_store.remaining_ttl(request.mfa_token)

    stored_ip = token_data.get("ip_address")
    if stored_ip and stored_ip != client_ip:
        from src.app.core.logger import get_logger

        logger = get_logger(__name__)
        logger.warning("MFA IP mismatch", stored_ip=stored_ip, client_ip=client_ip, user_id=token_data.get("user_id"))
        # SECURITY FIX (M-1) + S-M3: reject IP mismatch so a stolen 5-minute
        # MFA pending token cannot be completed from another machine. Escape
        # hatch for networks that legitimately rotate the client IP mid-login
        # (mobile carriers): the token was minted at login, so a fresh token
        # seen from a new IP is a plausible mid-login rotation — accept while
        # the token is younger than MFA_IP_BINDING_GRACE_SECONDS. A
        # near-expiry token from a new IP is not plausible and is rejected.
        if settings.auth.MFA_ENFORCE_IP_BINDING:
            grace = settings.auth.MFA_IP_BINDING_GRACE_SECONDS
            token_age = settings.auth.MFA_PENDING_EXPIRY_SECONDS - remaining_ttl
            within_grace = grace > 0 and 0 <= token_age <= grace
            if not within_grace:
                raise UnauthorizedError(
                    "MFA verification rejected: IP address changed since login. Please try again."
                )

    user_id = token_data.get("user_id")
    username = token_data.get("username")

    if not user_id or not username:
        raise UnauthorizedError("Invalid MFA pending token")

    # Verify MFA code
    verify_request = MFAVerifyRequest(
        totp_token=request.totp_token,
        backup_code=request.backup_code,
    )
    verify_result = await service.verify_totp_token(
        user_id=user_id,
        request=verify_request,
        db=db,
        prevent_replay=True,
    )

    if not verify_result.get("valid"):
        raise UnauthorizedError("Invalid MFA code")

    # R1-1: consume the pending token only after the second factor passed.
    # A concurrent double-submit that already consumed it lands here with
    # None → rejected, so exactly one issuance happens per pending token.
    # (If issuance below fails after this consume, the user re-logs in —
    # the safe direction: never two sessions from one pending token.)
    consumed = await token_store.consume(request.mfa_token)
    if consumed is None:
        raise UnauthorizedError("Invalid or expired MFA pending token")

    # Get user info for token generation
    user_service = get_user_service(db)
    user = await user_service.get_user_auth_info(username=username)

    if not user or user.get("is_deleted") or not user.get("is_active", False):
        raise UnauthorizedError("User account is no longer active")

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
