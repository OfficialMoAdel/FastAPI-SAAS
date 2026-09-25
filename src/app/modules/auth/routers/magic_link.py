"""Magic Link authentication API router.

HTTP endpoints for magic link passwordless authentication.
Follows the API contract defined in auth-api.yaml.
"""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from src.app.core.security.rate_policy import check_security_budget
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.auth.schemas import (
    LoginMFAResponse,
    LoginTokenResponse,
    MagicLinkSendRequest,
    MagicLinkSendResponse,
    MagicLinkVerifyRequest,
)
from src.app.modules.auth.services.magic_link import MagicLinkService, get_magic_link_service
from src.app.shared.exceptions import RateLimitExceededError

router = APIRouter(prefix="/auth/magic-link", tags=["magic-link"])


@router.post(
    "/send",
    response_model=MagicLinkSendResponse,
    summary="Send magic link",
    description="Request a magic link to be sent to the specified email. "
    "Returns success even if email doesn't exist to prevent enumeration.",
    responses={
        200: {"description": "Magic link sent (or not, to prevent enumeration)"},
        422: {"description": "Invalid email format"},
        429: {"description": "Rate limit exceeded (3/min per email)"},
    },
)
async def send_magic_link(
    request: Request,
    body: MagicLinkSendRequest,
    service: Annotated[MagicLinkService, Depends(get_magic_link_service)],
) -> MagicLinkSendResponse:
    """Send a magic link to the specified email address.

    This endpoint always returns success to prevent email enumeration.
    If the email exists, a magic link will be sent.
    The link expires after 15 minutes and can only be used once.

    Rate limited to 3 requests per minute per email address (the
    ``magic_link_send`` budget in ``settings.rate_limit.SECURITY_BUDGETS``,
    keyed on a hashed email — the same anti-enumeration semantics as the
    dependency this replaces).
    """
    # Anti-enumeration budget: keyed on the email hash, not the IP, so one
    # sender cannot mail-bomb a victim's inbox by rotating IPs (and a
    # shared NAT cannot lock out an office).
    email_hash = hashlib.sha256(body.email.lower().strip().encode()).hexdigest()[:16]
    verdict = await check_security_budget("magic_link_send", f"email:{email_hash}")
    if verdict.is_limited:
        raise RateLimitExceededError(
            f"Too many magic link requests. Please try again in {verdict.period} seconds.",
            retry_after=verdict.retry_after_seconds(),
        )

    # Extract client info for audit logging
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")

    return await service.send_magic_link(
        email=body.email,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post(
    "/verify",
    response_model=LoginTokenResponse | LoginMFAResponse,
    summary="Verify magic link",
    description="Verify a magic link token and receive authentication tokens.",
    responses={
        200: {"description": "Token verified, auth tokens returned. If MFA is enabled, returns MFA pending token."},
        400: {"description": "Invalid or expired token"},
        410: {"description": "Token already used"},
        422: {"description": "Invalid token format"},
    },
)
async def verify_magic_link(
    request: Request,
    body: MagicLinkVerifyRequest,
    response: Response,
    service: Annotated[MagicLinkService, Depends(get_magic_link_service)],
) -> LoginTokenResponse | LoginMFAResponse:
    """Verify a magic link token and return authentication tokens.

    The token is consumed after first use (one-time use).
    Returns JWT access tokens (and sets secure HTTP-only refresh cookie) on successful verification.

    Possible errors:
    - 400: Token is invalid or expired
    - 410: Token was already used
    """
    from src.app.core.utils.ip_utils import get_client_ip

    ip_address = get_client_ip(request)
    result = await service.verify_magic_link(
        token=body.token, ip_address=ip_address, response=response, request=request
    )
    # Cookie was written by issue_session inside the service when the
    # response was threaded through; nothing left to do here but return.
    return result
