"""User schemas for API request/response validation."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from ...core.schemas import PersistentDeletion, TimestampSchema, UUIDSchema
from .._base import BaseCreate, BaseList, BaseRead, BaseUpdate

# =============================================================================
# P2-1: Pre-compiled password validation regex patterns (performance optimization)
# Compiling once at module load vs. re-compiling on every validation call
# =============================================================================
_PASSWORD_LOWERCASE_RE = re.compile(r"[a-z]")
_PASSWORD_UPPERCASE_RE = re.compile(r"[A-Z]")
_PASSWORD_DIGIT_RE = re.compile(r"\d")
_PASSWORD_SPECIAL_RE = re.compile(r"[^a-zA-Z0-9]")

# =============================================================================
# Base Schema (shared fields)
# =============================================================================


class UserBase(BaseModel):
    """Base schema with common user fields."""

    name: Annotated[str, Field(min_length=2, max_length=30, examples=["User Userson"])]
    username: Annotated[str, Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["userson"])]
    email: Annotated[EmailStr, Field(examples=["user.userson@example.com"])]


# =============================================================================
# Internal Schemas (for ORM/service layer)
# =============================================================================


class User(TimestampSchema, UserBase, UUIDSchema, PersistentDeletion):
    """Internal user schema with all fields including sensitive data."""

    profile_image_url: Annotated[str, Field(default="https://www.profileimageurl.com")]
    hashed_password: str
    is_superuser: bool = False
    tier_id: int | None = None


class UserCreateInternal(UserBase):
    """Internal schema for creating users with hashed password."""

    hashed_password: str
    tenant_id: int | None = None


class UserUpdateInternal(BaseUpdate):
    """Internal schema for updating users with timestamp."""

    name: Annotated[str | None, Field(min_length=2, max_length=30, examples=["User Userberg"], default=None)]
    username: Annotated[
        str | None,
        Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["userberg"], default=None),
    ]
    email: Annotated[EmailStr | None, Field(examples=["user.userberg@example.com"], default=None)]
    profile_image_url: Annotated[
        str | None,
        Field(
            pattern=r"^(https?|ftp)://[^\s/$.?#].[^\s]*$",
            examples=["https://www.profileimageurl.com"],
            default=None,
        ),
    ]
    updated_at: datetime | None = None


# =============================================================================
# API Schemas (for external API)
# =============================================================================


class UserRead(BaseRead):
    """API response schema for user data."""

    name: str
    username: str
    email: EmailStr
    profile_image_url: str
    tier_id: int | None
    tenant_id: int


class UserCreate(UserBase, BaseCreate):
    """API request schema for creating a user."""

    password: Annotated[
        str,
        Field(
            min_length=8,
            max_length=128,
            examples=["Str1ngst!"],
            description="Must contain at least one lowercase, uppercase, digit, and special character",
        ),
    ]

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """Validate password contains required character types.

        Uses pre-compiled regex patterns for better performance.
        """
        if not _PASSWORD_LOWERCASE_RE.search(v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not _PASSWORD_UPPERCASE_RE.search(v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not _PASSWORD_DIGIT_RE.search(v):
            raise ValueError("Password must contain at least one digit")
        if not _PASSWORD_SPECIAL_RE.search(v):
            raise ValueError("Password must contain at least one special character")
        return v


class UserUpdate(BaseUpdate):
    """API request schema for updating a user."""

    name: Annotated[str | None, Field(min_length=2, max_length=30, examples=["User Userberg"], default=None)]
    username: Annotated[
        str | None,
        Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["userberg"], default=None),
    ]
    email: Annotated[EmailStr | None, Field(examples=["user.userberg@example.com"], default=None)]
    profile_image_url: Annotated[
        str | None,
        Field(
            pattern=r"^(https?|ftp)://[^\s/$.?#].[^\s]*$",
            examples=["https://www.profileimageurl.com"],
            default=None,
        ),
    ]


class UserTierUpdate(BaseModel):
    """Schema for updating user tier."""

    tier_id: int


class UserDelete(BaseUpdate):
    """Schema for soft-deleting a user."""

    is_deleted: bool
    deleted_at: datetime


class UserRestoreDeleted(BaseModel):
    """Schema for restoring a soft-deleted user."""

    is_deleted: bool


class UserList(BaseList[UserRead]):
    """Paginated list of users."""

    pass


class UserDeleteResponse(BaseModel):
    """Response schema for user deletion."""

    message: str = "User deleted successfully"


class UserRestoreResponse(BaseModel):
    """Response schema for user restoration."""

    message: str = "User restored successfully"
