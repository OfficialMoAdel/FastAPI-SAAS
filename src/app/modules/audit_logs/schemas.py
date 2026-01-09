"""Pydantic schemas for audit logging."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .._base import BaseCreate, BaseList, BaseReadUUID

# =============================================================================
# Audit Log Schemas
# =============================================================================


class AuditLogBase(BaseModel):
    """Base audit log schema."""

    action: str = Field(..., description="Action performed (e.g., user.created)")
    entity_type: str | None = Field(None, description="Type of entity affected")
    entity_id: UUID | None = Field(None, description="ID of entity affected")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class AuditLogCreate(AuditLogBase, BaseCreate):
    """Schema for creating audit log."""

    tenant_id: int
    user_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogRead(BaseReadUUID):
    """Schema for audit log response."""

    tenant_id: int
    user_id: int | None
    action: str
    entity_type: str | None
    entity_id: UUID | None
    details: dict[str, Any]
    ip_address: str | None
    user_agent: str | None


# Backward compatibility alias
AuditLogResponse = AuditLogRead


class AuditLogList(BaseList[AuditLogRead]):
    """Schema for paginated audit log list."""

    pass


# Backward compatibility alias
AuditLogListResponse = AuditLogList


# =============================================================================
# Filter & Query Schemas
# =============================================================================


class AuditLogFilter(BaseModel):
    """Schema for filtering audit logs."""

    action: str | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    user_id: int | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    page: int = Field(1, ge=1)
    per_page: int = Field(50, ge=1, le=100)  # Renamed from page_size


# =============================================================================
# Event Bus Schemas
# =============================================================================


class AuditLogEventData(BaseModel):
    """Schema for validating audit log event queue data."""

    tenant_id: str
    user_id: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None

    @field_validator("tenant_id", "user_id", mode="before")
    @classmethod
    def validate_integer_ids(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            try:
                int(v)
                return v
            except ValueError:
                raise ValueError(f"Invalid integer ID format: {v}")
        raise ValueError(f"ID must be int or str, got {type(v).__name__}")
