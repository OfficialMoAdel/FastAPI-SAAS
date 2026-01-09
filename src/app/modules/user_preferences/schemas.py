"""Pydantic schemas for user preferences operations.

Task: T088 [US9] Create Pydantic schemas with validation

Schemas for:
- UserPreferences CRUD operations
- Preference update (partial updates)
- Preference response
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .._base import BaseCreate, BaseRead, BaseUpdate

# ============================================================================
# Enums
# ============================================================================


class ThemeOption(str, Enum):
    """Valid theme options."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


# ============================================================================
# Request Schemas
# ============================================================================


class UserPreferencesUpdate(BaseUpdate):
    """Schema for updating user preferences (partial update).

    All fields are optional - only include fields to update.
    """

    theme: ThemeOption | None = Field(
        None,
        description="UI theme preference: system, light, or dark",
    )
    language: str | None = Field(
        None,
        min_length=2,
        max_length=2,
        pattern=r"^[a-z]{2}$",
        description="ISO 639-1 language code (e.g., 'en', 'fr', 'de')",
    )
    email_notifications: bool | None = Field(
        None,
        description="Enable email notifications",
    )
    push_notifications: bool | None = Field(
        None,
        description="Enable push notifications",
    )
    timezone: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="IANA timezone (e.g., 'UTC', 'America/New_York')",
    )
    date_format: str | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="Date format string (e.g., 'YYYY-MM-DD', 'MM/DD/YYYY')",
    )
    currency: str | None = Field(
        None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code (e.g., 'USD', 'EUR', 'GBP')",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        """Validate timezone string."""
        if v is None:
            return v
        # Basic validation - could use pytz for stricter validation
        # For now, just ensure it's not empty and has reasonable format
        if not v or len(v.strip()) == 0:
            raise ValueError("Timezone cannot be empty")
        return v.strip()


# ============================================================================
# Response Schemas
# ============================================================================


class UserPreferencesResponse(BaseModel):
    """Response for user preferences."""

    model_config = ConfigDict(from_attributes=True)

    theme: str = Field(..., description="UI theme preference")
    language: str = Field(..., description="Language code")
    email_notifications: bool = Field(..., description="Email notifications enabled")
    push_notifications: bool = Field(..., description="Push notifications enabled")
    timezone: str = Field(..., description="User timezone")
    date_format: str = Field(..., description="Date format preference")
    currency: str = Field(..., description="Currency code")
    updated_at: datetime = Field(..., description="Last update timestamp")


# ============================================================================
# Internal/CRUD Schemas
# ============================================================================


class UserPreferencesCreate(BaseCreate):
    """Schema for creating user preferences record."""

    user_id: int = Field(..., description="User ID")
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="Preferences JSONB object",
    )


class UserPreferencesRead(BaseRead):
    """Schema for reading user preferences from database.

    Inherits from BaseRead: id, created_at, updated_at
    """

    user_id: int
    preferences: dict[str, Any]
