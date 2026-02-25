"""Auth API router - HTTP handlers delegating to service layer."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.security import oauth2_scheme
from ...core.security.session_manager import SessionManager
from ...core.utils.cache import async_get_redis
from ..users.models import User
from ..users.schemas import UserCreate, UserRead
from ..users.service import UserService
from .schemas import (
    EmailVerificationConfirm,
    EmailVerificationRequest,
    EmailVerificationResponse,
    ForgotPasswordResponse,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
    RefreshTokenResponse,
    Token,
)
from .service import AuthService


def _get_auth_service() -> AuthService:
    """Get AuthService instance."""
    return AuthService()


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Get UserService instance."""
    return UserService(db)


router = APIRouter(prefix="/account", tags=["account"])


@router.post("/login", response_model=Token)
async def login_for_access_token(
    response: Response,
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    redis: Annotated[Redis, Depends(async_get_redis)],
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> dict[str, str]:
    """Authenticate user and return access token."""
    return await auth_service.login(form_data.username, form_data.password, response, request, db, redis)


@router.post("/register", response_model=UserRead, status_code=201)
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
    return await auth_service.refresh_access_token(refresh_token, response, db)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    request: Request,
    access_token: str = Depends(oauth2_scheme),
    refresh_token: str | None = Cookie(None, alias="refresh_token"),
    db: AsyncSession = Depends(async_get_db),
    auth_service: AuthService = Depends(_get_auth_service),
) -> dict[str, str]:
    """Logout user by blacklisting tokens."""
    return await auth_service.logout(access_token, refresh_token, response, request, db)


@router.patch("/password", response_model=PasswordChangeResponse)
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


@router.get("/sessions")
async def list_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> list[dict]:
    """List all active sessions for current user."""
    session_manager = SessionManager(redis)
    sessions = await session_manager.get_active_sessions(user_id=current_user["id"])
    return sessions


@router.get("/sessions/count")
async def count_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> dict[str, int]:
    """Count active sessions for current user."""
    session_manager = SessionManager(redis)
    count = await session_manager.count_active_sessions(user_id=current_user["id"])
    return {"count": count}
