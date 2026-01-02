"""Auth service layer - encapsulates all authentication business logic."""

from datetime import timedelta

from fastapi import Response
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import EnvironmentOption, settings
from ...core.exceptions.http_exceptions import UnauthorizedException
from ...core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    TokenType,
    blacklist_token,
    blacklist_tokens,
    create_access_token,
    create_refresh_token,
    verify_password,
    verify_token,
)
from ...modules.users.crud import crud_users


class AuthService:
    """Service class for authentication-related business logic."""


    async def authenticate_user(
        self, username_or_email: str, password: str, db: AsyncSession
    ) -> dict | bool:
        """Authenticate user by checking database and password."""
        if "@" in username_or_email:
            db_user = await crud_users.get(db=db, email=username_or_email, is_deleted=False)
        else:
            db_user = await crud_users.get(db=db, username=username_or_email, is_deleted=False)

        if not db_user:
            return False

        if not await verify_password(password, db_user["hashed_password"]):
            return False

        return db_user

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
        if attempts and int(attempts) >= settings.AUTH_MAX_LOGIN_ATTEMPTS:
            lockout_minutes = settings.AUTH_LOCKOUT_DURATION_SECONDS // 60
            raise UnauthorizedException(
                f"Account locked due to too many failed attempts. Please try again in {lockout_minutes} minutes."
            )

        user = await self.authenticate_user(username_or_email=username, password=password, db=db)
        if not user:
            # Increment failed attempts
            new_attempts = await redis.incr(login_attempts_key)
            if new_attempts == 1:
                await redis.expire(login_attempts_key, settings.AUTH_LOCKOUT_DURATION_SECONDS)

            raise UnauthorizedException("Wrong username, email or password.")

        # Clear failed attempts on success
        await redis.delete(login_attempts_key)

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = await create_access_token(data={"sub": user["username"]}, expires_delta=access_token_expires)

        refresh_token = await create_refresh_token(data={"sub": user["username"]})
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

        # Cookie security settings based on environment
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=is_production,
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

        return {"access_token": access_token, "token_type": "bearer"}

    async def refresh_access_token(
        self, refresh_token: str | None, response: Response, db: AsyncSession
    ) -> dict[str, str]:
        """Refresh access token using refresh token.

        Implements rotation: invalidates old refresh token and issues a new one.
        """
        if not refresh_token:
            raise UnauthorizedException("Refresh token missing.")

        user_data = await verify_token(refresh_token, TokenType.REFRESH, db)
        if not user_data:
            raise UnauthorizedException("Invalid refresh token.")

        # Blacklist the used refresh token (Rotation)
        await blacklist_token(token=refresh_token, db=db)

        # Create new tokens
        new_access_token = await create_access_token(data={"sub": user_data.username_or_email})
        new_refresh_token = await create_refresh_token(data={"sub": user_data.username_or_email})

        # Cookie security settings based on environment
        max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        is_production = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=is_production,
            samesite="strict" if is_production else "lax",
            max_age=max_age,
        )

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
        import logging

        from ...core.events import EventPriority, event_bus
        from ...modules.users.crud import crud_users
        from .models import PasswordResetToken

        logger = logging.getLogger(__name__)

        user = await crud_users.get(db=db, email=email, is_deleted=False)

        # Always return success to prevent email enumeration
        if user:
            # Create password reset token (expires in 1 hour)
            reset_token = PasswordResetToken(user_id=user["id"])
            db.add(reset_token)
            await db.commit()
            await db.refresh(reset_token)

            logger.info("Password reset token created | user_id=%s | token_id=%s", user["id"], reset_token.id)

            # Send email via event bus
            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

            await event_bus.publish(
                "notification.send",
                {
                    "to_email": email,
                    "template_name": "password_reset",
                    "context": {
                        "username": user["username"],
                        "reset_link": reset_link,
                        "valid_minutes": 60,
                    },
                    "tenant_id": str(user["tenant_id"]),
                    "user_id": str(user["id"]),
                },
                priority=EventPriority.NORMAL,
            )

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
        import logging

        from sqlalchemy import select

        from ...core.security import get_password_hash
        from ...modules.users.crud import crud_users
        from .models import PasswordResetToken

        logger = logging.getLogger(__name__)

        # Find token in database
        result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token == token))
        reset_token = result.scalar_one_or_none()

        if not reset_token:
            raise UnauthorizedException("Invalid password reset token.")

        if not reset_token.is_valid:
            if reset_token.used:
                raise UnauthorizedException("This password reset token has already been used.")
            if reset_token.is_expired:
                raise UnauthorizedException("This password reset token has expired.")
            raise UnauthorizedException("Invalid password reset token.")

        # Get user
        user = await crud_users.get(db=db, id=reset_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Update password
        new_hashed = await get_password_hash(new_password)
        await crud_users.update(db=db, id=reset_token.user_id, object={"hashed_password": new_hashed})

        # Mark token as used
        reset_token.used = True
        await db.commit()

        logger.info("Password reset successful | user_id=%s | token_id=%s", reset_token.user_id, reset_token.id)

        return {"message": "Password reset successfully."}

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

        from ...core.events import EventPriority, event_bus
        from ...modules.users.crud import crud_users
        from .models import EmailVerificationToken

        user = await crud_users.get(db=db, email=email, is_deleted=False)

        if user and not user.get("is_email_verified", False):
            # Create verification token (defaults handle generation)
            token_model = EmailVerificationToken(user_id=user["id"])
            db.add(token_model)
            await db.commit()
            await db.refresh(token_model)

            verification_token = token_model.token

            # Send email via event bus
            verification_link = f"{settings.FRONTEND_URL}/verify-email?token={verification_token}"

            await event_bus.publish(
                "notification.send",
                {
                    "to_email": email,
                    "template_name": "email_verification",
                    "context": {
                        "username": user["username"],
                        "verification_link": verification_link,
                    },
                    "tenant_id": str(user["tenant_id"]),
                    "user_id": str(user["id"]),
                },
                priority=EventPriority.NORMAL,
            )

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
        from sqlalchemy import select

        from ...modules.users.crud import crud_users
        from .models import EmailVerificationToken

        # Find token in database
        result = await db.execute(select(EmailVerificationToken).where(EmailVerificationToken.token == token))
        verification_token = result.scalar_one_or_none()

        if not verification_token:
            raise UnauthorizedException("Invalid verification token.")

        if verification_token.is_expired:
            raise UnauthorizedException("Verification token has expired.")

        # Get user
        user = await crud_users.get(db=db, id=verification_token.user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("User not found.")

        # Update user status
        await crud_users.update(db=db, id=verification_token.user_id, object={"is_email_verified": True})

        # Delete token (it's single use)
        await db.delete(verification_token)
        await db.commit()

        return {"message": "Email verified successfully."}


# Singleton instance for import convenience
auth_service = AuthService()
