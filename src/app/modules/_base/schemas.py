"""Base Pydantic schemas for all feature modules.

Provides reusable base schemas that ensure consistency across all
feature modules for CRUD operations and API responses.

Usage:
    from src.app.modules._base import BaseCreate, BaseRead, BaseUpdate

    class UserCreate(BaseCreate):
        email: str
        name: str

    class UserRead(BaseRead):
        email: str
        name: str
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class BaseCreate(BaseModel):
    """Base schema for creating resources.

    All create request schemas should inherit from this class.
    Provides consistent configuration for all create operations.
    """

    model_config = {"from_attributes": True, "extra": "forbid"}


class BaseRead(BaseModel):
    """Base schema for reading resources with integer primary key.

    All read/response schemas with int PK should inherit from this class.
    Includes id and timestamps for audit purposes.
    """

    id: int
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BaseReadUUID(BaseModel):
    """Base schema for reading resources with UUID primary key.

    All read/response schemas with UUID PK should inherit from this class.
    Use for modules like audit_logs, webhooks, notifications.
    """

    id: UUID
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BaseUpdate(BaseModel):
    """Base schema for updating resources.

    All update request schemas should inherit from this class.
    All fields are optional to support partial updates.
    """

    model_config = {"from_attributes": True, "populate_by_name": True, "extra": "forbid"}


class BaseList(BaseModel, Generic[T]):
    """Base schema for paginated list responses.

    Generic type parameter T specifies the item type.

    Usage:
        class UserList(BaseList[UserRead]):
            pass
    """

    items: list[Any]  # Subclasses should override with specific type
    total: int
    page: int
    per_page: int
    has_next: bool
    has_previous: bool

    model_config = {"from_attributes": True}


class BasePagination(BaseModel):
    """Base pagination parameters for list requests.

    Provides consistent pagination defaults across all modules.
    """

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(default=20, ge=1, le=100, description="Items per page (max 100)")


__all__ = [
    "BaseCreate",
    "BaseRead",
    "BaseReadUUID",
    "BaseUpdate",
    "BaseList",
    "BasePagination",
]
