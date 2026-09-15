"""Auth API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.logger import get_logger
from src.app.core.security import oauth2_scheme
from src.app.core.security.login_helpers import (
    clear_refresh_cookies,
    get_refresh_cookie,
    set_refresh_cookie,
)
from src.app.core.security.session_manager import SessionManager
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils.cache import async_get_redis
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.auth import AuthService
from src.app.modules.auth.schemas import (
    EmailVerificationConfirm,
    EmailVerificationRequest,
    EmailVerificationResponse,
    ForgotPasswordResponse,
    LoginMFAResponse,
    LoginTokenResponse,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
    RefreshTokenResponse,
    SessionCountResponse,
    SessionInfo,
    SessionListResponse,
)
from src.app.modules.auth.services.email_verification import EmailVerificationService
from src.app.modules.auth.services.passwords import PasswordLifecycleService
from src.app.modules.users import UserService
from src.app.modules.users import get_user_service as _get_user_service
from src.app.modules.users.schemas import UserCreate, UserReadPublic
from src.app.shared.exceptions import UnauthorizedError

logger = get_logger(__name__)


def _get_auth_service() -> AuthService:
    """Get AuthService instance."""
    return AuthService()


def _get_password_lifecycle_service() -> PasswordLifecycleService:
    """Get PasswordLifecycleService instance (owns password lifecycle)."""
    return PasswordLifecycleService()


def _get_email_verification_service() -> EmailVerificationService:
    """Get EmailVerificationService instance (owns email verification)."""
    return EmailVerificationService()


def _get_session_manager(redis: Annotated[Redis, Depends(async_get_redis)]) -> SessionManager:
    """Dependency provider for SessionManager."""
    return SessionManager(redis)


router = APIRouter(prefix="/account", tags=["account"])

from src.app.modules.auth.rate_limit import check_auth_rate_limit


@router.post(
    "/login",
    response_model=LoginTokenResponse | LoginMFAResponse,
    dependencies=[Depends(check_auth_rate_limit)],
)
async def login_for_access_token(
    response: Response,
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> Any:
    """Authenticate user and return access token."""
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    result = await auth_service.login(
        form_data.username,
        form_data.password,
        response=response,
        request=request,
        db=db,
        redis=redis,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    # Cookie written by the issuance seam; nothing left to do but return.
    return result


@router.post(
    "/register",
    response_model=UserReadPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(check_auth_rate_limit)],
)
async def register(
    request: Request,
    user: UserCreate,
    user_service: Annotated[UserService, Depends(_get_user_service)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Register a new user account.

    S-M2 signup gate: while ``AUTH_OPEN_REGISTRATION`` is False (default),
    self-service registration only bootstraps the FIRST user of a tenant.
    Joining an existing tenant requires an admin-created account — the old
    behavior let anyone register into any tenant by guessing its
    ``X-Tenant-ID`` slug.
    """
    from src.app.modules.auth.services.email_verification import make_email_verifier
    from src.app.modules.auth.services.registration_gate import is_self_registration_allowed

    if not await is_self_registration_allowed(db):
        tenant = get_current_tenant()
        logger.warning(
            "registration_gate_rejected",
            path="/account/register",
            tenant_id=str(tenant.id) if tenant is not None else None,
        )
        raise ForbiddenException(
            detail="Registration is closed for this tenant. Ask an administrator to create your account."
        )

    return await user_service.create(user, email_verifier=make_email_verifier(db))


@router.post("/refresh", response_model=RefreshTokenResponse, dependencies=[Depends(check_auth_rate_limit)])
async def refresh_access_token(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    auth_service: AuthService = Depends(_get_auth_service),
) -> dict[str, str]:
    """Refresh access token using refresh token from cookie."""
    refresh_token = get_refresh_cookie(request)
    if not refresh_token:
        raise UnauthorizedError("Refresh token missing")
    result = await auth_service.refresh_access_token(
        refresh_token,
        response=response,
        db=db,
        request=request,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if isinstance(result, dict) and result.get("refresh_token"):
        set_refresh_cookie(response, result["refresh_token"])
    return result


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={401: {"description": "Unauthorized"}},
    dependencies=[Depends(check_auth_rate_limit)],
)
async def logout(
    response: Response,
    request: Request,
    access_token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(async_get_db),
    auth_service: AuthService = Depends(_get_auth_service),
) -> Any:
    """Logout user by blacklisting tokens."""
    refresh_token = get_refresh_cookie(request)
    result = await auth_service.logout(
        access_token,
        refresh_token,
        response=response,
        request=request,
        db=db,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    clear_refresh_cookies(response)
    return result


@router.patch(
    "/password",
    response_model=PasswordChangeResponse,
    responses={401: {"description": "Unauthorized"}, 403: {"description": "Forbidden"}},
    dependencies=[Depends(check_auth_rate_limit)],
)
async def change_password(
    request: Request,
    ctx: RequestContextDep,
    body: PasswordChangeRequest,
    service: Annotated[PasswordLifecycleService, Depends(_get_password_lifecycle_service)],
) -> PasswordChangeResponse:
    """Change current user's password."""
    result = await service.change_password(
        user_id=ctx.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
        db=ctx.db,
    )
    return PasswordChangeResponse(**result)


@router.post("/forgot-password", response_model=ForgotPasswordResponse, dependencies=[Depends(check_auth_rate_limit)])
async def forgot_password(
    body: PasswordResetRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[PasswordLifecycleService, Depends(_get_password_lifecycle_service)],
) -> ForgotPasswordResponse:
    """Request password reset email."""
    result = await service.request_password_reset(email=body.email, db=db)
    return ForgotPasswordResponse(**result)


@router.post("/reset-password", response_model=PasswordResetResponse, dependencies=[Depends(check_auth_rate_limit)])
async def reset_password(
    body: PasswordResetConfirm,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[PasswordLifecycleService, Depends(_get_password_lifecycle_service)],
) -> PasswordResetResponse:
    """Reset password using reset token."""
    result = await service.reset_password(token=body.token, new_password=body.new_password, db=db)
    return PasswordResetResponse(**result)


@router.post(
    "/send-verification", response_model=EmailVerificationResponse, dependencies=[Depends(check_auth_rate_limit)]
)
async def send_verification_email(
    body: EmailVerificationRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[EmailVerificationService, Depends(_get_email_verification_service)],
) -> EmailVerificationResponse:
    """Send email verification link."""
    result = await service.send_verification_email(email=body.email, db=db)
    return EmailVerificationResponse(**result)


@router.post("/verify-email", response_model=EmailVerificationResponse, dependencies=[Depends(check_auth_rate_limit)])
async def verify_email(
    body: EmailVerificationConfirm,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    service: Annotated[EmailVerificationService, Depends(_get_email_verification_service)],
) -> EmailVerificationResponse:
    """Verify email using verification token."""
    result = await service.verify_email(token=body.token, db=db)
    return EmailVerificationResponse(**result)


@router.get("/sessions", response_model=SessionListResponse, responses={401: {"description": "Unauthorized"}})
async def list_sessions(
    ctx: RequestContextDep,
    session_manager: Annotated[SessionManager, Depends(_get_session_manager)],
) -> SessionListResponse:
    """List all active sessions for current user."""
    sessions = await session_manager.get_active_sessions(user_id=ctx.user_id)

    from datetime import datetime

    session_infos = []
    for s in sessions:
        device_info_raw = s.get("device_info") or ""
        ip_address = None
        user_agent = None

        # Parse JSON format if possible, otherwise fallback to legacy delimited format
        try:
            from src.app.core.json_utils import json_loads

            device_info = json_loads(device_info_raw)
            if isinstance(device_info, dict):
                user_agent = device_info.get("user_agent")
                ip_address = device_info.get("ip")
        except Exception:
            # Fallback for legacy sessions
            if " | IP: " in device_info_raw:
                user_agent, ip_address = device_info_raw.split(" | IP: ", 1)
            else:
                user_agent = device_info_raw if device_info_raw else None

        created_at_str = s.get("created_at")
        try:
            if isinstance(created_at_str, str):
                created_at_ts = int(datetime.fromisoformat(created_at_str).timestamp())
            else:
                created_at_ts = 0
        except (ValueError, TypeError):
            created_at_ts = 0

        # BUG FIX: last_accessed_at was always set equal to created_at because the
        # session dict had no "last_accessed" key. Now we read it when present and
        # fall back to created_at for sessions that pre-date this fix.
        last_accessed_str = s.get("last_accessed", created_at_str)
        try:
            if isinstance(last_accessed_str, str):
                last_accessed_ts = int(datetime.fromisoformat(last_accessed_str).timestamp())
            else:
                last_accessed_ts = created_at_ts
        except (ValueError, TypeError):
            last_accessed_ts = created_at_ts

        session_infos.append(
            SessionInfo(
                id=s.get("session_id", ""),
                user_id=ctx.user_id,
                created_at=created_at_ts,
                last_accessed_at=last_accessed_ts,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

    return SessionListResponse(sessions=session_infos)


@router.get("/sessions/count", response_model=SessionCountResponse, responses={401: {"description": "Unauthorized"}})
async def count_sessions(
    ctx: RequestContextDep,
    session_manager: Annotated[SessionManager, Depends(_get_session_manager)],
) -> SessionCountResponse:
    """Count active sessions for current user."""
    count = await session_manager.count_active_sessions(user_id=ctx.user_id)
    return SessionCountResponse(count=count)


from src.app.modules.auth.routers.magic_link import router as magic_link_router
from src.app.modules.auth.routers.mfa import router as mfa_router
from src.app.modules.auth.routers.passkey import router as passkey_router
from src.app.modules.auth.routers.sso import router as sso_router
from src.app.modules.auth.routers.sso_settings import router as sso_settings_router

auth_router = router

__all__ = [
    "router",
    "auth_router",
    "magic_link_router",
    "mfa_router",
    "passkey_router",
    "sso_router",
    "sso_settings_router",
]
