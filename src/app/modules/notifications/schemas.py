"""Pydantic schemas for notifications."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class NotificationBase(BaseModel):
    """Base notification schema."""

    template_name: str = Field(..., description="Template name to use")
    to_email: EmailStr = Field(..., description="Recipient email")
    subject: str | None = Field(None, description="Email subject override")
    context: dict[str, Any] = Field(default_factory=dict, description="Template context variables")


class NotificationCreate(NotificationBase):
    """Schema for creating notification."""

    tenant_id: UUID
    user_id: UUID | None = None


class NotificationResponse(BaseModel):
    """Schema for notification response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    template_name: str
    to_email: str
    subject: str
    status: str
    sent_at: datetime | None
    error_message: str | None
    created_at: datetime


class NotificationSendRequest(BaseModel):
    """Schema for sending notification via API."""

    template_name: str = Field(..., description="Template name (e.g., 'welcome', 'password_reset')")
    to_email: EmailStr = Field(..., description="Recipient email address")
    context: dict[str, Any] = Field(default_factory=dict, description="Variables for template rendering")
    subject_override: str | None = Field(None, description="Override default subject")


class NotificationListResponse(BaseModel):
    """Schema for paginated notification list."""

    items: list[NotificationResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class EmailTemplateOverrideBase(BaseModel):
    """Base schema for email template override."""

    template_name: str
    subject_override: str | None = None
    # Limit content to 50KB to prevent DoS
    content: str = Field(..., max_length=51200, description="HTML template content (max 50KB)")


class EmailTemplateOverrideCreate(EmailTemplateOverrideBase):
    """Schema for creating email template override."""

    tenant_id: UUID


class EmailTemplateOverrideUpdate(BaseModel):
    """Schema for updating email template override."""

    subject_override: str | None = None
    content: str | None = None


class EmailTemplateOverrideResponse(BaseModel):
    """Schema for email template override response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    template_name: str
    subject_override: str | None
    content: str
    created_at: datetime
    updated_at: datetime
