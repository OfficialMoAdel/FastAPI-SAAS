"""Auth service layer - encapsulates all authentication business logic."""

from datetime import timedelta
from typing import Optional

from fastapi import Response
from jose import JWTError
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

        Returns
        -------
        dict[str, str]
            Access token and token type

        Raises
        ------
        UnauthorizedException
            If credentials are invalid
        """
        user = await authenticate_user(username_or_email=username, password=password, db=db)
        if not user:
            raise UnauthorizedException("Wrong username, email or password.")

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

    async def refresh_access_token(self, refresh_token: Optional[str], db: AsyncSession) -> dict[str, str]:
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
        refresh_token: Optional[str],
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


# Singleton instance for import convenience
auth_service = AuthService()
