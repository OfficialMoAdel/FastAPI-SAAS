"""Auth service layer - encapsulates all authentication business logic."""

from datetime import timedelta

from fastapi import Response
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.exceptions.http_exceptions import UnauthorizedException
from ...core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    TokenType,
    authenticate_user,
    blacklist_tokens,
    create_access_token,
    create_refresh_token,
    verify_token,
)


class AuthService:
    """Service class for authentication-related business logic."""

    async def login(
        self,
        username: str,
        password: str,
        response: Response,
        db: AsyncSession,
        redis: Redis,
    ) -> dict[str, str]:
        """Authenticate user and generate tokens.

        Parameters
        ----------
        username : str
            Username or email
        password : str
            User password
        response : Response
            FastAPI response object for setting cookies
        db : AsyncSession
            Database session
        redis : Redis
            Redis client for account lockout

        Returns
        -------
        dict[str, str]
            Access token and token type

        Raises
        ------
        UnauthorizedException
            If credentials are invalid or account is locked
        """
        # Check for account lockout
        login_attempts_key = f"login_attempts:{username}"
        attempts = await redis.get(login_attempts_key)
        if attempts and int(attempts) >= 5:
            raise UnauthorizedException(
                "Account locked due to too many failed attempts. Please try again in 15 minutes."
            )

        user = await authenticate_user(username_or_email=username, password=password, db=db)
        if not user:
            # Increment failed attempts
            new_attempts = await redis.incr(login_attempts_key)
            if new_attempts == 1:
                await redis.expire(login_attempts_key, 900)  # 15 minutes

            raise UnauthorizedException("Wrong username, email or password.")

        # Clear failed attempts on success
        await redis.delete(login_attempts_key)

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = await create_access_token(data={"sub": user["username"]}, expires_delta=access_token_expires)

        refresh_token = await create_refresh_token(data={"sub": user["username"]})
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=max_age,
        )

        return {"access_token": access_token, "token_type": "bearer"}

    async def refresh_access_token(self, refresh_token: str | None, db: AsyncSession) -> dict[str, str]:
        """Refresh access token using refresh token.

        Parameters
        ----------
        refresh_token : Optional[str]
            Refresh token from cookie
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            New access token and token type

        Raises
        ------
        UnauthorizedException
            If refresh token is missing or invalid
        """
        if not refresh_token:
            raise UnauthorizedException("Refresh token missing.")

        user_data = await verify_token(refresh_token, TokenType.REFRESH, db)
        if not user_data:
            raise UnauthorizedException("Invalid refresh token.")

        new_access_token = await create_access_token(data={"sub": user_data.username_or_email})
        return {"access_token": new_access_token, "token_type": "bearer"}

    async def logout(
        self,
        access_token: str,
        refresh_token: str | None,
        response: Response,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Logout user by blacklisting tokens.

        Parameters
        ----------
        access_token : str
            Access token to blacklist
        refresh_token : Optional[str]
            Refresh token from cookie
        response : Response
            FastAPI response object for deleting cookies
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If refresh token is missing or invalid
        """
        try:
            if not refresh_token:
                raise UnauthorizedException("Refresh token not found.")

            await blacklist_tokens(access_token=access_token, refresh_token=refresh_token, db=db)
            response.delete_cookie(key="refresh_token")
            return {"message": "Logged out successfully"}

        except JWTError:
            raise UnauthorizedException("Invalid token.")

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Change user password.

        Parameters
        ----------
        user_id : int
            The user's ID
        current_password : str
            Current password for verification
        new_password : str
            New password to set
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If current password is incorrect
        """
        from ...core.security import get_password_hash, verify_password
        from ...modules.users.crud import crud_users

        user = await crud_users.get(db=db, id=user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Verify current password
        if not await verify_password(current_password, user["hashed_password"]):
            raise UnauthorizedException("Current password is incorrect.")

        # Hash and update new password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=user_id, object={"hashed_password": new_hashed})

        return {"message": "Password changed successfully"}

    async def request_password_reset(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Request password reset (sends email with reset token).

        Parameters
        ----------
        email : str
            User's email address
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message (always returns success for security)
        """
        import secrets
        from datetime import UTC, datetime, timedelta

        from ...modules.users.crud import crud_users

        user = await crud_users.get(db=db, email=email, is_deleted=False)

        # Always return success to prevent email enumeration
        if user:
            # Generate reset token (valid for 1 hour)
            secrets.token_urlsafe(32)
            datetime.now(UTC) + timedelta(hours=1)

            # Store token in database (would need a password_reset_tokens table)
            # For now, we'll use a simple approach - in production, store in DB
            # This is a placeholder - actual implementation needs a tokens table

            # TODO: Send email with reset link containing the token
            # await notification_service.send_password_reset_email(email, reset_token)

        return {"message": "If an account exists with that email, a reset link has been sent."}

    async def reset_password(
        self,
        token: str,
        new_password: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Reset password using reset token.

        Parameters
        ----------
        token : str
            Password reset token
        new_password : str
            New password to set
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If token is invalid or expired
        """
        # TODO: Validate token from password_reset_tokens table
        # For now, this is a placeholder that shows the structure
        raise UnauthorizedException("Password reset tokens not yet implemented. Please contact support.")

    async def send_verification_email(
        self,
        email: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Send email verification link.

        Parameters
        ----------
        email : str
            User's email address
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message
        """
        import secrets
        from datetime import UTC, datetime, timedelta

        from ...modules.users.crud import crud_users

        user = await crud_users.get(db=db, email=email, is_deleted=False)

        if user and not user.get("is_email_verified", False):
            # Generate verification token (valid for 24 hours)
            secrets.token_urlsafe(32)
            datetime.now(UTC) + timedelta(hours=24)

            # TODO: Store token in database (would need email_verification_tokens table)
            # TODO: Send email with verification link containing the token
            # await notification_service.send_verification_email(email, verification_token)

        return {"message": "If the email is registered and unverified, a verification link has been sent."}

    async def verify_email(
        self,
        token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Verify email using verification token.

        Parameters
        ----------
        token : str
            Email verification token
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        UnauthorizedException
            If token is invalid or expired
        """
        # TODO: Validate token from email_verification_tokens table
        # For now, this is a placeholder that shows the structure
        raise UnauthorizedException("Email verification tokens not yet implemented. Please contact support.")


# Singleton instance for import convenience
auth_service = AuthService()
