"""Pydantic schemas for audit logging."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditLogBase(BaseModel):
    """Base audit log schema."""

    action: str = Field(..., description="Action performed (e.g., user.created)")
    entity_type: str | None = Field(None, description="Type of entity affected")
    entity_id: UUID | None = Field(None, description="ID of entity affected")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class AuditLogCreate(AuditLogBase):
    """Schema for creating audit log."""

    tenant_id: UUID
    user_id: UUID | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogResponse(AuditLogBase):
    """Schema for audit log response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    """Schema for paginated audit log list."""

    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class AuditLogFilter(BaseModel):
    """Schema for filtering audit logs."""

    action: str | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    user_id: UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=100)
