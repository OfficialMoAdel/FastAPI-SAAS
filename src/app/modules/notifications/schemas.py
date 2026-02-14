"""Pydantic schemas for notifications."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from .._base import BaseCreate, BaseList, BaseReadUUID, BaseUpdate

# =============================================================================
# Notification Schemas
# =============================================================================


class NotificationBase(BaseModel):
    """Base notification schema."""

    template_name: str = Field(..., description="Template name to use")
    to_email: EmailStr = Field(..., description="Recipient email")
    subject: str | None = Field(None, description="Email subject override")
    context: dict[str, Any] = Field(default_factory=dict, description="Template context variables")


class NotificationCreate(NotificationBase, BaseCreate):
    """Schema for creating notification."""

    tenant_id: UUID
    user_id: int | None = None


class NotificationRead(BaseReadUUID):
    """Schema for notification response."""

    tenant_id: UUID
    user_id: int | None
    template_name: str
    to_email: str
    subject: str
    status: str
    sent_at: datetime | None
    error_message: str | None


# Backward compatibility alias
NotificationResponse = NotificationRead


class NotificationList(BaseList[NotificationRead]):
    """Schema for paginated notification list."""

    pass


# Backward compatibility alias
NotificationListResponse = NotificationList


class NotificationSendRequest(BaseModel):
    """Schema for sending notification via API."""

    template_name: str = Field(..., description="Template name (e.g., 'welcome', 'password_reset')")
    to_email: EmailStr = Field(..., description="Recipient email address")
    context: dict[str, Any] = Field(default_factory=dict, description="Variables for template rendering")
    subject_override: str | None = Field(None, description="Override default subject")


# =============================================================================
# Email Template Override Schemas
# =============================================================================


class EmailTemplateOverrideBase(BaseModel):
    """Base schema for email template override."""

    template_name: str = Field(..., description="Template name to override")
    subject_override: str | None = Field(None, description="Custom subject line")
    # Limit content to 50KB to prevent DoS
    content: str = Field(..., max_length=51200, description="HTML template content (max 50KB)")


class EmailTemplateOverrideCreate(EmailTemplateOverrideBase, BaseCreate):
    """Schema for creating email template override."""

    tenant_id: UUID


class EmailTemplateOverrideUpdate(BaseUpdate):
    """Schema for updating email template override."""

    subject_override: str | None = None
    content: str | None = Field(None, max_length=51200)


class EmailTemplateOverrideRead(BaseReadUUID):
    """Schema for email template override response."""

    tenant_id: UUID
    template_name: str
    subject_override: str | None
    content: str


# Backward compatibility alias
EmailTemplateOverrideResponse = EmailTemplateOverrideRead
