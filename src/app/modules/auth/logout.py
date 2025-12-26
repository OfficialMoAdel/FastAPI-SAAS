from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Response
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...core.db.crud_token_blacklist import crud_token_blacklist
from ...core.exceptions.http_exceptions import UnauthorizedException
from ...core.security import (
    TokenType,
    verify_token,
    oauth2_scheme,
    blacklist_tokens,
)

router = APIRouter(prefix="/auth", tags=["auth"])
