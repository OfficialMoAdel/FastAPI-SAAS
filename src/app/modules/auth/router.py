"""Auth API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.security import oauth2_scheme
from src.app.core.security.session_manager import SessionManager
from src.app.core.utils.cache import async_get_redis
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
from src.app.modules.auth.service import AuthService
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreate, UserRead
from src.app.modules.users.service import UserService


def _get_auth_service() -> AuthService:
    """Get AuthService instance."""
    return AuthService()


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Get UserService instance."""
    return UserService(db)


router = APIRouter(prefix="/account", tags=["account"])


@router.post("/login", response_model=LoginTokenResponse | LoginMFAResponse)
async def login_for_access_token(
    response: Response,
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> Any:
    """Authenticate user and return access token."""
    return await auth_service.login(form_data.username, form_data.password, response, request, db, redis)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    user: UserCreate,
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> User:
    """Register a new user account."""
    return await user_service.create(user)


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_access_token(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    auth_service: AuthService = Depends(_get_auth_service),
) -> dict[str, str]:
    """Refresh access token using refresh token from cookie."""
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")
    return await auth_service.refresh_access_token(refresh_token, response, db)


@router.post("/logout", response_model=LogoutResponse, responses={401: {"description": "Unauthorized"}})
async def logout(
    response: Response,
    request: Request,
    access_token: str = Depends(oauth2_scheme),
    refresh_token: str | None = Cookie(None, alias="refresh_token"),
    db: AsyncSession = Depends(async_get_db),
    auth_service: AuthService = Depends(_get_auth_service),
) -> Any:
    """Logout user by blacklisting tokens."""
    return await auth_service.logout(access_token, refresh_token, response, request, db)


@router.patch(
    "/password",
    response_model=PasswordChangeResponse,
    responses={401: {"description": "Unauthorized"}, 403: {"description": "Forbidden"}},
)
async def change_password(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    body: PasswordChangeRequest,
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> PasswordChangeResponse:
    """Change current user's password."""
    result = await auth_service.change_password(
        user_id=current_user["id"],
        current_password=body.current_password,
        new_password=body.new_password,
        db=db,
    )
    return PasswordChangeResponse(**result)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    body: PasswordResetRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> ForgotPasswordResponse:
    """Request password reset email."""
    result = await auth_service.request_password_reset(email=body.email, db=db)
    return ForgotPasswordResponse(**result)


@router.post("/reset-password", response_model=PasswordResetResponse)
async def reset_password(
    body: PasswordResetConfirm,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> PasswordResetResponse:
    """Reset password using reset token."""
    result = await auth_service.reset_password(token=body.token, new_password=body.new_password, db=db)
    return PasswordResetResponse(**result)


@router.post("/send-verification", response_model=EmailVerificationResponse)
async def send_verification_email(
    body: EmailVerificationRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> EmailVerificationResponse:
    """Send email verification link."""
    result = await auth_service.send_verification_email(email=body.email, db=db)
    return EmailVerificationResponse(**result)


@router.post("/verify-email", response_model=EmailVerificationResponse)
async def verify_email(
    body: EmailVerificationConfirm,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> EmailVerificationResponse:
    """Verify email using verification token."""
    result = await auth_service.verify_email(token=body.token, db=db)
    return EmailVerificationResponse(**result)


@router.get("/sessions", response_model=SessionListResponse, responses={401: {"description": "Unauthorized"}})
async def list_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> SessionListResponse:
    """List all active sessions for current user."""
    session_manager = SessionManager(redis)
    sessions = await session_manager.get_active_sessions(user_id=current_user["id"])

    from datetime import datetime

    session_infos = []
    for s in sessions:
        device_info_raw = s.get("device_info") or ""
        ip_address = None
        user_agent = None

        # Parse JSON format if possible, otherwise fallback to legacy delimited format
        try:
            import json
            device_info = json.loads(device_info_raw)
            if isinstance(device_info, dict):
                user_agent = device_info.get("user_agent")
                ip_address = device_info.get("ip")
        except (json.JSONDecodeError, TypeError):
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
                user_id=current_user["id"],
                created_at=created_at_ts,
                last_accessed_at=last_accessed_ts,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

    return SessionListResponse(sessions=session_infos)


@router.get("/sessions/count", response_model=SessionCountResponse, responses={401: {"description": "Unauthorized"}})
async def count_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> SessionCountResponse:
    """Count active sessions for current user."""
    session_manager = SessionManager(redis)
    count = await session_manager.count_active_sessions(user_id=current_user["id"])
    return SessionCountResponse(count=count)
