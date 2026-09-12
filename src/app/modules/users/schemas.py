"""User schemas for API request/response validation."""

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.app.core.config import settings
from src.app.core.schemas import PersistentDeletion, TimestampSchema, UUIDSchema
from src.app.modules._base import BaseCreate, BaseList, BaseRead, BaseUpdate

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
    username: Annotated[
        str,
        Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["userson"]),
    ]
    email: Annotated[EmailStr, Field(examples=["user.userson@example.com"])]

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str | None) -> str | None:
        """Normalize email address to lowercase to prevent duplicates."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("username")
    @classmethod
    def validate_username_not_numeric(cls, v: str) -> str:
        """Ensure username is not entirely numeric."""
        if v.isdigit():
            raise ValueError("Username cannot consist entirely of numbers")
        return v


# =============================================================================
# Internal Schemas (for ORM/service layer)
# =============================================================================


class User(TimestampSchema, UserBase, UUIDSchema, PersistentDeletion):
    """Internal user schema with all fields including sensitive data."""

    profile_image_url: Annotated[str, Field(default="https://www.profileimageurl.com")]
    hashed_password: str
    is_superuser: bool = False
    tier_id: int | None = None
    tenant_id: UUID
    is_email_verified: bool = False
    token_version: int = 0

    # Allow validating directly from SQLAlchemy ORM instances
    model_config = ConfigDict(from_attributes=True)


class UserCreateInternal(UserBase):
    """Internal schema for creating users with hashed password."""

    hashed_password: str
    tenant_id: UUID | None = None


class UserUpdateInternal(BaseUpdate):
    """Internal schema for updating users with timestamp."""

    name: Annotated[str | None, Field(min_length=2, max_length=30, examples=["User Userberg"], default=None)]
    username: Annotated[
        str | None,
        Field(min_length=2, max_length=20, pattern=r"^[a-z0-9]+$", examples=["userberg"], default=None),
    ]
    email: Annotated[EmailStr | None, Field(examples=["user.userberg@example.com"], default=None)]

    @field_validator("username")
    @classmethod
    def validate_username_not_numeric(cls, v: str | None) -> str | None:
        """Ensure username is not entirely numeric."""
        if v and v.isdigit():
            raise ValueError("Username cannot consist entirely of numbers")
        return v

    profile_image_url: Annotated[
        str | None,
        Field(
            pattern=r"^https://[^\s/$.?#].[^\s]*$",
            examples=["https://www.profileimageurl.com"],
            default=None,
        ),
    ]
    updated_at: datetime | None = None


# =============================================================================
# API Schemas (for external API)
# =============================================================================


class UserReadPublic(BaseRead):
    """Public API response schema for user data.

    S-7: Deliberately excludes privilege/tenancy fields (``is_superuser``,
    ``tenant_id``) so they cannot be enumerated from public/self endpoints.
    Admin-gated endpoints and internal code use :class:`UserRead`, which adds
    those fields back.
    """

    name: str
    username: str
    email: EmailStr
    profile_image_url: str
    tier_id: int | None
    is_email_verified: bool = False
    is_active: bool = True


class UserRead(UserReadPublic):
    """Full API response schema for user data (admin/internal).

    S-7: Adds privilege/tenancy fields on top of :class:`UserReadPublic`.
    Used by admin-gated endpoints and as the internal CRUD projection schema —
    the router IDOR/tenant check reads ``tenant_id`` from this representation
    before FastAPI serializes the response, so it must stay complete.
    """

    tenant_id: UUID
    is_superuser: bool = False


class UserCreate(UserBase, BaseCreate):
    """API request schema for creating a user."""

    password: Annotated[
        str,
        Field(
            min_length=8,
            max_length=72,
            examples=["Ex4mplePw!a9Z"],  # C-1 hygiene: not a git-history-leaked value
            description="Must contain at least one lowercase, uppercase, digit, and special character",
        ),
    ]

    @field_validator("username")
    @classmethod
    def validate_username_not_reserved(cls, v: str) -> str:
        """Validate username is not a reserved system name.

        SECURITY FIX: Prevents users from registering reserved system usernames
        that could be used for phishing or confusion attacks.
        """
        if v.lower() in settings.app.RESERVED_USERNAMES:
            raise ValueError(f"Username '{v}' is reserved and cannot be used")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """Validate password length and required character types.

        Length floor lives here (not only in Field constraints) so every
        server-side path that calls this validator (register, change,
        reset) enforces it. Uses pre-compiled regex patterns for
        better performance.
        """
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
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
            pattern=r"^https://[^\s/$.?#].[^\s]*$",
            examples=["https://www.profileimageurl.com"],
            default=None,
        ),
    ]

    @field_validator("username")
    @classmethod
    def validate_reserved_usernames(cls, v: str | None) -> str | None:
        """Prevent updating to a reserved username."""
        if v is None:
            return v
        if v.lower() in settings.app.RESERVED_USERNAMES:
            raise ValueError(f"Username '{v}' is reserved.")
        return v


class UserTierUpdate(BaseModel):
    """Schema for updating user tier."""

    tier_id: int


class UserStatusUpdate(BaseModel):
    """Schema for activating/deactivating a user (superuser only)."""

    is_active: bool


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


class UserUpdateResponse(BaseModel):
    """Response schema for user update (PATCH/PUT)."""

    message: str = "User updated successfully"


class UserTierResponse(BaseModel):
    """Response schema for user tier information."""

    tier_id: int | None = None
    tier_name: str | None = None
    tier_price: float | None = None
    tier_currency: str | None = None
