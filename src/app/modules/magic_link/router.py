"""Magic Link authentication API router.

HTTP endpoints for magic link passwordless authentication.
Follows the API contract defined in auth-api.yaml.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from src.app.api.dependencies import rate_limiter_dependency
from src.app.modules.auth.schemas import LoginMFAResponse, LoginTokenResponse
from src.app.modules.magic_link.schemas import MagicLinkSendRequest, MagicLinkSendResponse, MagicLinkVerifyRequest
from src.app.modules.magic_link.service import MagicLinkService, get_magic_link_service

router = APIRouter(prefix="/auth/magic-link", tags=["magic-link"])


@router.post(
    "/send",
    response_model=MagicLinkSendResponse,
    summary="Send magic link",
    description="Request a magic link to be sent to the specified email. "
    "Returns success even if email doesn't exist to prevent enumeration.",
    dependencies=[Depends(rate_limiter_dependency)],
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

    Rate limited to 3 requests per minute per email address.
    """
    # Extract client info for audit logging
    ip_address = request.client.host if request.client else "unknown"
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
    dependencies=[Depends(rate_limiter_dependency)],
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
    return await service.verify_magic_link(token=body.token, ip_address=ip_address, response=response)
