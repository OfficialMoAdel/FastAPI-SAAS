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
    # Notification preference fields (added per mdl1.2.3)
    email_notifications_enabled: bool | None = Field(
        None,
        description="Enable email notifications delivery",
    )
    push_notifications_enabled: bool | None = Field(
        None,
        description="Enable push notifications delivery",
    )
    disabled_notification_types: list[str] | None = Field(
        None,
        description="List of notification type keys to disable (e.g., ['marketing', 'newsletter'])",
    )
    quiet_hours_enabled: bool | None = Field(
        None,
        description="Enable quiet hours - suppress notifications during specified hours",
    )
    quiet_hours_start: str | None = Field(
        None,
        pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$",
        description="Quiet hours start time in HH:MM format (e.g., '22:00')",
    )
    quiet_hours_end: str | None = Field(
        None,
        pattern=r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$",
        description="Quiet hours end time in HH:MM format (e.g., '08:00')",
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

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_quiet_hours_format(cls, v: str | None) -> str | None:
        """Validate quiet hours time format."""
        if v is None:
            return v
        # Field pattern already validates HH:MM format
        # Additional validation for logical consistency could be added
        return v

    @field_validator("quiet_hours_end")
    @classmethod
    def validate_quiet_hours_range(cls, v: str | None, info) -> str | None:
        """Validate quiet hours start and end are not equal."""
        if v is None:
            return v

        # Get quiet_hours_start value if it exists
        start = info.data.get("quiet_hours_start")
        if start and v:
            if start == v:
                raise ValueError("quiet_hours_start and quiet_hours_end cannot be equal")

        return v


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
    # Notification preference fields (added per mdl1.2.3)
    email_notifications_enabled: bool = Field(
        default=True,
        description="Email notifications delivery enabled",
    )
    push_notifications_enabled: bool = Field(
        default=True,
        description="Push notifications delivery enabled",
    )
    disabled_notification_types: list[str] = Field(
        default_factory=list,
        description="List of disabled notification type keys",
    )
    quiet_hours_enabled: bool = Field(
        default=False,
        description="Quiet hours enabled",
    )
    quiet_hours_start: str | None = Field(
        default=None,
        description="Quiet hours start time (HH:MM)",
    )
    quiet_hours_end: str | None = Field(
        default=None,
        description="Quiet hours end time (HH:MM)",
    )
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


class UserPreferencesUpdateInternal(BaseModel):
    """Internal schema for updating user preferences."""

    preferences: dict[str, Any] | None = None


class UserPreferencesDeleteInternal(BaseModel):
    """Internal schema for deleting user preferences."""

    pass
