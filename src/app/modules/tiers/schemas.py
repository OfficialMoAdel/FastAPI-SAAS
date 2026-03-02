"""Tier schemas for API request/response validation.

Inherits from base schemas to ensure consistency and reduce duplication.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from src.app.core.schemas import TimestampSchema
from src.app.modules._base.schemas import BaseCreate, BaseRead, BaseUpdate


class TierBase(BaseModel):
    """Base schema with common tier fields."""

    name: Annotated[str, Field(examples=["free"])]


class Tier(TimestampSchema, TierBase):
    """Full tier schema with timestamps (for internal use)."""

    pass


class TierRead(BaseRead, TierBase):
    """Schema for tier API responses.

    Inherits id, created_at, updated_at from BaseRead.
    """

    pass


class TierCreate(BaseCreate, TierBase):
    """Schema for creating a new tier."""

    pass


class TierCreateInternal(TierCreate):
    """Internal schema for tier creation with additional fields."""

    pass


class TierUpdate(BaseUpdate):
    """Schema for updating an existing tier.

    All fields optional for partial updates.
    """

    name: str | None = None


class TierUpdateInternal(TierUpdate):
    """Internal schema for tier updates with timestamp."""

    updated_at: datetime


class TierDelete(BaseModel):
    """Schema for tier deletion confirmation."""

    pass
