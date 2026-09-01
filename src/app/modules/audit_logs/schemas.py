"""Pydantic schemas for audit logging."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from src.app.modules._base import BaseCreate, BaseList, BaseReadUUID

# =============================================================================
# Audit Log Schemas
# =============================================================================


class AuditLogBase(BaseModel):
    """Base audit log schema."""

    action: str = Field(..., description="Action performed (e.g., user.created)")
    entity_type: str | None = Field(None, description="Type of entity affected")
    entity_id: str | None = Field(None, description="ID of entity affected")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional details")


class AuditLogCreate(AuditLogBase, BaseCreate):
    """Schema for creating audit log."""

    tenant_id: UUID
    user_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogRead(BaseReadUUID):
    """Schema for audit log response."""

    tenant_id: UUID
    user_id: int | None
    action: str
    entity_type: str | None
    entity_id: str | None
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


# M5: Typed response schema for export count endpoint
class ExportCountResponse(BaseModel):
    """Schema for export count response."""

    count: int = Field(..., description="Number of records matching the filter criteria")
    max_export_limit: int = Field(..., description="Maximum records allowed per export")
    can_export: bool = Field(..., description="Whether the count is within export limit")


class AuditLogFilter(BaseModel):
    """Schema for filtering audit logs."""

    action: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
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

    tenant_id: UUID
    user_id: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases_and_types(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize resource_type -> entity_type
            if data.get("entity_type") is None and data.get("resource_type") is not None:
                data["entity_type"] = str(data["resource_type"])
            elif data.get("entity_type") is not None:
                data["entity_type"] = str(data["entity_type"])

            if data.get("resource_type") is not None:
                data["resource_type"] = str(data["resource_type"])

            # Normalize resource_id -> entity_id
            if data.get("entity_id") is None and data.get("resource_id") is not None:
                data["entity_id"] = str(data["resource_id"])
            elif data.get("entity_id") is not None:
                data["entity_id"] = str(data["entity_id"])

            if data.get("resource_id") is not None:
                data["resource_id"] = str(data["resource_id"])

            # Normalize user_id to string if present
            if data.get("user_id") is not None:
                data["user_id"] = str(data["user_id"])
        return data

    @field_validator("tenant_id", mode="before")
    @classmethod
    def validate_tenant_id(cls, v: Any) -> UUID:
        if isinstance(v, UUID):
            return v
        if v is None or v == "" or v == "0" or v == 0:
            return UUID(int=0)
        if isinstance(v, str):
            try:
                return UUID(v)
            except ValueError:
                return UUID(int=0)
        if isinstance(v, int):
            return UUID(int=v)
        raise ValueError(f"tenant_id must be UUID, str, or int, got {type(v).__name__}")
