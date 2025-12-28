import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
RESERVED_SLUGS = frozenset({"host", "admin", "api"})


class TenantBase(BaseModel):
    """Base schema for tenant data."""

    name: Annotated[str, Field(min_length=1, max_length=100, examples=["Acme Corporation"])]


class TenantCreate(TenantBase):
    """Schema for creating a new tenant."""

    model_config = ConfigDict(extra="forbid")

    slug: Annotated[
        str,
        Field(
            min_length=3,
            max_length=50,
            pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
            examples=["acme-corp"],
            description="URL-safe identifier for X-Tenant-ID header (lowercase alphanumeric with hyphens)",
        ),
    ]

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate slug format and reserved values."""
        v = v.lower()
        if not SLUG_PATTERN.match(v):
            raise ValueError("Slug must be lowercase alphanumeric with hyphens, starting and ending with alphanumeric")
        if v in RESERVED_SLUGS:
            raise ValueError(f"Slug '{v}' is reserved and cannot be used")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name is not empty after trimming."""
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty or whitespace only")
        return v


class TenantUpdate(BaseModel):
    """Schema for updating an existing tenant."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(min_length=1, max_length=100, default=None)]
    is_active: Annotated[bool | None, Field(default=None)]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        """Validate name is not empty after trimming."""
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Name cannot be empty or whitespace only")
        return v


class TenantUpdateInternal(TenantUpdate):
    """Internal schema for updating tenant with timestamp."""

    updated_at: datetime


class TenantResponse(BaseModel):
    """Schema for tenant response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


class TenantListResponse(BaseModel):
    """Schema for paginated tenant list response."""

    data: list[TenantResponse]
    total: int
    page: int
    items_per_page: int
